"""Connected mode's decision for one tool call.

Reads are allowed for the client the session was launched for, on the orgs that
client's consent lists; check-only deploys and test runs are allowed and logged;
an org write is allowed only by consuming an approval the consultant granted for
that exact call; a browser action needs a granted browser window; anything the
gate cannot check asks the consultant (and is refused when prompts are skipped);
approval administration is refused. See docs/connected-approval.md."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from . import approval, consent, gate, workspace as ws
from .connected_routes import Route, classify, is_simple

RANK = {"allow": 0, "ask": 1, "deny": 2}
PREFIX = "Connected mode: "
# Permission modes in which a host prompt is skipped or decided without the consultant.
NO_PROMPT_MODES = ("bypassPermissions", "auto", "dontAsk")


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str


def ask_json(reason: str) -> str:
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                              "permissionDecisionReason": reason}})


def _worst(decisions: list[Decision]) -> Decision:
    return max(decisions, key=lambda d: RANK[d.action]) if decisions else Decision("allow", "")


def _deny(text: str) -> Decision:
    return Decision("deny", PREFIX + text)


def _bound_client(env, workspace: Path) -> str | None:
    value = env.get("TORQUE_CLIENT")
    if not value:
        return None
    try:
        slug = ws.slug_for(value)
    except ws.WorkspaceError:
        return None
    return slug if (workspace / "clients" / slug / "client.json").is_file() else None


def _guarded(workspace: Path, bound: str | None) -> list[Path]:
    clients = workspace / "clients"
    if not bound:
        return [clients]
    try:
        return [p for p in clients.iterdir() if p.name != bound and (p.is_dir() or p.is_symlink())]
    except OSError:
        return [clients]


def _route_client(route: Route) -> str | None:
    if route.client is None:
        return None
    try:
        return ws.slug_for(route.client)
    except ws.WorkspaceError:
        return "<invalid>"


def decide_connected(tool_name, tool_input, workspace, cwd, *, env, permission_mode=None, session_id=None,
                     tool_use_id=None) -> Decision:
    workspace = Path(workspace)
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    oversized = gate._oversized_reason(tool_name, tool_input)
    if oversized:
        return _deny(oversized.replace("Build-only mode: ", ""))
    bound = _bound_client(env, workspace)
    ok, reason = gate._decide(tool_name, tool_input, workspace, cwd, org_rules=False,
                              guarded=_guarded(workspace, bound))
    if not ok:
        text = reason.replace("Build-only mode: ", "")
        if "client context" in text and bound:
            text += f" This session is bound to {bound}; other clients' folders stay out of it."
        return _deny(text)
    routes = classify(tool_name, tool_input)
    if all(r.kind == "local" and _route_client(r) in (None, bound) for r in routes):
        return Decision("allow", "")
    if not bound:
        return _deny("no client is bound to this session. The consultant starts it with "
                     "`torque launch --workspace W --client NAME`.")
    config = ws.load_workspace(workspace)[1]
    item = consent.load_consent(workspace, bound)
    problems = consent.consent_problems(item)
    allowed_data = consent.data_allowed(item)
    command = tool_input.get("command") if isinstance(tool_input.get("command"), str) else ""
    writes = [r for r in routes if r.kind == "org_write"]
    decisions: list[Decision] = []
    for route in routes:
        client = _route_client(route)
        if client not in (None, bound):
            decisions.append(_deny(f"this session is bound to {bound}; it cannot act for {route.client}."))
        elif route.kind == "local":
            decisions.append(Decision("allow", ""))
        elif route.kind == "admin":
            decisions.append(_deny(f"{route.detail}: only the consultant runs this, in their own terminal."))
        elif route.kind == "no_org":
            decisions.append(_deny(f"{route.detail}: name exactly one org with --target-org (-o); "
                                   "the default org is never used in connected mode."))
        elif route.kind == "unverifiable":
            text = (f"the gate cannot check what `{route.detail}` does. Read it (and any script it runs) "
                    "before allowing it; it must not write to an org without an approval.")
            if permission_mode in NO_PROMPT_MODES:
                decisions.append(_deny(text + f" Refused because this session skips prompts ({permission_mode})."))
            else:
                decisions.append(Decision("ask", PREFIX + text))
        elif problems:
            decisions.append(_deny(f"{bound}'s consent is not usable: " + "; ".join(problems) + "."))
        elif route.org is not None and consent.approved_org(item, route.org) is None:
            decisions.append(_deny(f"org {route.org!r} is not in {bound}'s consent."))
        elif route.kind in ("read", "check_only"):
            if route.data == "records" and "records" not in allowed_data:
                decisions.append(_deny(f"{bound}'s consent does not cover record data."))
            elif route.data == "debug_logs" and "debug_logs" not in allowed_data:
                decisions.append(_deny(f"{bound}'s consent does not cover debug logs."))
            else:
                if route.kind == "check_only":
                    approval.log_activity(workspace, bound, {"action": "check-only", "org_alias": route.org,
                                                             "command": command or tool_name,
                                                             "session_id": session_id, "tool_use_id": tool_use_id})
                decisions.append(Decision("allow", ""))
        elif route.kind == "browser_write":
            orgs = [route.org] if route.org else [o["alias"] for o in item.get("approved_orgs", [])]
            window = next((w for w in (approval.active_browser_approval(workspace, bound, org, config=config,
                                                                         session_id=session_id,
                                                                         tool_use_id=tool_use_id)
                                       for org in orgs) if w), None)
            if window:
                approval.log_activity(workspace, bound, {"action": "browser", "approval_id": window["id"],
                                                         "org_alias": window["org_alias"], "tool": tool_name,
                                                         "session_id": session_id, "tool_use_id": tool_use_id})
                decisions.append(Decision("allow", ""))
            else:
                decisions.append(_deny("browser changes need a browser window: "
                                       "`torque approval request --browser --purpose TEXT ...`, "
                                       "then the consultant grants it. Reading pages is fine."))
        elif route.kind == "org_write":
            if len(writes) != 1 or (command and not is_simple(command)):
                decisions.append(_deny("run one approved write command on its own, with nothing chained, "
                                       "piped or redirected."))
                continue
            if tool_name.startswith("mcp__"):
                key = approval.call_key_for_mcp(tool_name, tool_input)
            else:
                key = approval.call_key_for_command(command)
            used, why = approval.consume(workspace, bound, key, route.org, config=config, session_id=session_id,
                                         tool_use_id=tool_use_id, cwd=cwd)
            decisions.append(Decision("allow", "") if used else _deny(
                f"{why}. Ask for it with `torque approval request ... -- <this exact command>`, "
                "then stop until the consultant grants it."))
        else:
            decisions.append(_deny(f"unrecognized route {route.kind}."))
    return _worst(decisions)
