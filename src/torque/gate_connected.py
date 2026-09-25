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
import os
from pathlib import Path
import re
import shutil
import urllib.parse

from . import approval, consent, gate, workspace as ws
from .connected_routes import BROWSER_SERVER, Route, classify, is_simple

RANK = {"allow": 0, "ask": 1, "deny": 2}
PREFIX = "Connected mode: "
# Permission modes in which the host shows the consultant a prompt. Any other named
# mode (bypassPermissions, auto, dontAsk, or one this version does not know) is refused
# for routes the gate cannot check. A missing mode is treated as a prompting one: hosts
# and callers that do not send the field (the doctor probe, older hosts) keep the ask.
PROMPT_MODES = ("default", "acceptEdits", "plan")


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    approved: str | None = None   # the approval (or browser window) this allow used


def ask_json(reason: str) -> str:
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                              "permissionDecisionReason": reason}})


def allow_json(reason: str) -> str:
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                              "permissionDecisionReason": reason}})


def unattended(root: Path) -> bool:
    """The workspace runs the unattended permission profile, which is honored only
    for a tier 2 workspace whose approver is a named delegate."""
    from . import delegation, permissions
    try:
        config = ws.load_workspace(root)[1]
    except (OSError, ws.WorkspaceError):
        return False
    return permissions.load_profile(root) == "unattended" and delegation.delegated_tier2(config)


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


FILE_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def _protected_roots(env) -> tuple[list[Path], list[Path]]:
    """(folders no recognized tool may name, extra folders no file tool may write).

    The first holds the Salesforce CLI's credentials, alias and config files and
    its installation and plugins: changing them would send an approved command
    to another org or run other code under an approved command's name. The
    second is every folder on PATH this account can write, where a program of
    the same name as an approved one could be placed."""
    home = Path(os.path.realpath(str(env.get("HOME") or Path.home())))
    named = [home / ".sf", home / ".sfdx", home / ".local" / "share" / "sf", home / ".config" / "sf",
             home / ".cache" / "sf", Path(os.path.realpath(str(approval.key_path())))]
    executable = shutil.which("sf", path=env.get("PATH"))
    if executable:
        real = Path(os.path.realpath(executable))
        named.append(real.parent.parent if real.parent.name == "bin" else real.parent)
    writable = []
    for entry in (env.get("PATH") or "").split(os.pathsep):
        if entry and os.path.isdir(entry) and os.access(entry, os.W_OK):
            writable.append(Path(os.path.realpath(entry)))
    return named, writable


def _protected_reason(tool_name: str, tool_input: dict, cwd: Path, env) -> str:
    named, writable = _protected_roots(env)
    targets: list[Path] = []
    if tool_name in FILE_WRITE_TOOLS or tool_name in gate.READ_TOOLS:
        key = gate.PATH_TOOLS.get(tool_name, "file_path")
        raw = tool_input.get(key) or tool_input.get("notebook_path")
        if isinstance(raw, str) and raw:
            target = gate._resolve(cwd, raw)
            if any(gate._is_within(target, root) for root in named):
                return ("the approval key and the Salesforce CLI's credentials, aliases, configuration and "
                        "installation stay out of it")
            if tool_name in FILE_WRITE_TOOLS and any(gate._is_within(target, root) for root in writable):
                return ("writing into a folder on PATH could replace a program an approved command runs; "
                        "the consultant installs programs")
        return ""
    for text in [tool_input["command"]] if isinstance(tool_input.get("command"), str) else \
            list(gate._command_strings(tool_input)):
        for toks, _ in gate._segments_with_separators(gate._expand_home_in_command(text)):
            for tok in toks:
                paths, _ = gate._token_paths(tok, cwd)
                targets += paths
    if any(gate._is_within(t, root) for t in targets for root in named):
        return "the Salesforce CLI's credentials, aliases, configuration and installation stay out of it"
    return ""


# My Domain host suffixes, with the kind of org they belong to.
_SF_SUFFIXES = tuple((f".{kind}.{rest}" if kind != "prod" else f".{rest}", kind)
                     for kind in ("sandbox", "develop", "scratch", "prod")
                     for rest in ("my.salesforce.com", "lightning.force.com", "my.salesforce-setup.com",
                                  "vf.force.com", "my.site.com", "file.force.com"))
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def org_key(url: str) -> tuple[str, str] | None:
    """(My Domain name, org kind) for a Salesforce org URL or host; None for any
    other host (including Salesforce sites that are not an org, such as login)."""
    host = (urllib.parse.urlsplit(url).hostname if "://" in url else url.split("/")[0]) or ""
    host = host.casefold().rstrip(".")
    for suffix, kind in _SF_SUFFIXES:
        if host.endswith(suffix) and len(host) > len(suffix):
            return host[:-len(suffix)], kind
    return None


def _same_org(found: tuple[str, str], approved: tuple[str, str]) -> bool:
    """The host belongs to the approved org: same kind, same My Domain name, or that
    name followed by a Visualforce or package suffix (`acme--c`)."""
    return found[1] == approved[1] and (found[0] == approved[0] or found[0].startswith(approved[0] + "--"))


def browser_org(item: dict | None, url: str) -> tuple[bool, str | None]:
    """(is a Salesforce org URL, the approved alias it belongs to or None)."""
    found = org_key(url)
    if found is None:
        return False, None
    for org in consent._orgs(item):
        approved = org_key(org.get("instance_url") or "")
        if approved and _same_org(found, approved):
            return True, org["alias"]
    return True, None


def _route_client(route: Route) -> str | None:
    if route.client is None:
        return None
    if route.client == "*":
        return "*"
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
    protected = _protected_reason(tool_name, tool_input, Path(cwd), env)
    if protected:
        return _deny(protected + ".")
    routes = classify(tool_name, tool_input)
    if all(r.kind == "local" and _route_client(r) in (None, bound) for r in routes):
        return Decision("allow", "")
    from . import permissions
    if permissions.load_profile(workspace) == "invalid":
        # V2 I1: fail closed before anything below can consume an approval. A
        # damaged or unreadable sidecar is never read as the interactive profile.
        return _deny(f"the permission profile ({permissions.PROFILE_FILE}) is unreadable or invalid. "
                     "The consultant reruns the permissions setup step.")
    unbound = _deny("no client is bound to this session. The consultant starts it with "
                    "`torque launch --workspace W --client NAME`.")
    config = ws.load_workspace(workspace)[1]
    item = consent.load_consent(workspace, bound) if bound else None
    problems = consent.consent_problems(item, client=bound)
    allowed_data = consent.data_allowed(item)
    command = tool_input.get("command") if isinstance(tool_input.get("command"), str) else ""
    writes = [r for r in routes if r.kind == "org_write"]
    decisions: list[Decision] = []
    pending_write: Route | None = None
    after_allow: list = []
    approved_id: str | None = None
    skipping = bool(permission_mode) and permission_mode not in PROMPT_MODES
    browser_tool = tool_name.startswith("mcp__") and bool(BROWSER_SERVER.search(tool_name.split("__")[1]
                                                                                 if tool_name.count("__") > 1 else ""))
    if browser_tool:
        # Browser tools may read and navigate, but not to a Salesforce org outside the consent.
        for url in [u for v in gate._string_values(tool_input) for u in _URL_RE.findall(v)]:
            is_org, alias = browser_org(item, url)
            if is_org and (alias is None or not bound):
                decisions.append(_deny(f"{url.split('?')[0]} is a Salesforce org that is not in "
                                       f"{bound or 'a bound client'}'s consent."))
    for route in routes:
        client = _route_client(route)
        if not bound and (client is not None or route.kind not in ("local", "admin", "unverifiable")):
            decisions.append(unbound)
        elif client not in (None, bound):
            decisions.append(_deny(f"this session is bound to {bound}; it cannot act for {route.client}."))
        elif route.kind not in ("local", "admin") and route.org is not None and problems:
            decisions.append(_deny(f"{bound}'s consent is not usable: " + "; ".join(problems) + "."))
        elif route.kind not in ("local", "admin") and route.org is not None \
                and consent.approved_org(item, route.org) is None:
            decisions.append(_deny(f"org {route.org!r} is not in {bound}'s consent."))
        elif skipping and route.kind in ("unverifiable", "org_write", "browser_write"):
            decisions.append(_deny(f"`{route.detail}` needs the consultant, and this session skips prompts "
                                   f"({permission_mode}). Use the default, acceptEdits or plan mode."))
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
                    after_allow.append(lambda org=route.org: approval.log_activity(
                        workspace, bound, {"action": "check-only", "org_alias": org, "command": command or tool_name,
                                           "session_id": session_id, "tool_use_id": tool_use_id}))
                decisions.append(Decision("allow", ""))
        elif route.kind == "browser_write" and not route.org:
            # A browser MCP or devtools tool cannot show which org its page is in after
            # navigation and redirects, so it never makes changes in connected mode.
            decisions.append(_deny("browser changes go through Torque's own browser, `torque browser ... "
                                   "--target-org ORG`, which checks each request's org against a granted "
                                   "window; browser tools here may read and navigate only."))
        elif route.kind == "browser_write":
            window = approval.find_browser_approval(workspace, bound, route.org, config=config)
            if window and route.headed and window.get("delegated") is not False:
                decisions.append(_deny(f"the browser window for {route.org} was granted by a delegated approver, "
                                       "so Torque's browser runs headless only: run it without --headed."))
            elif window:
                approved_id = window["id"]
                after_allow.append(lambda win=window: approval.note_browser_use(
                    workspace, bound, win, tool_name=tool_name, session_id=session_id, tool_use_id=tool_use_id))
                decisions.append(Decision("allow", ""))
            else:
                decisions.append(_deny(f"browser changes in {route.org} need a browser window for {route.org}: "
                                       "`torque approval request --browser --purpose TEXT ...`, "
                                       "then the consultant grants it."))
        elif route.kind == "org_write":
            if len(writes) != 1 or (command and not is_simple(command)):
                decisions.append(_deny("run one approved write command on its own, with nothing chained, "
                                       "piped or redirected."))
                continue
            # Consumed only after every other route is decided (below), so a call that is
            # denied for another reason never uses up its approval.
            pending_write = route
        else:
            decisions.append(_deny(f"unrecognized route {route.kind}."))
    worst = _worst(decisions)
    if worst.action != "allow":
        return worst
    if pending_write is not None:
        if tool_name.startswith("mcp__"):
            key = approval.call_key_for_mcp(tool_name, tool_input)
        else:
            key = approval.call_key_for_command(command)
        used, why = approval.consume(workspace, bound, key, pending_write.org, config=config,
                                     session_id=session_id, tool_use_id=tool_use_id, cwd=cwd)
        if not used:
            return _deny(f"{why}. Ask for it with `torque approval request ... -- <this exact command>`, "
                         "then stop until the consultant grants it.")
        approved_id = why
    try:
        for record in after_allow:
            record()
    except (OSError, ws.WorkspaceError) as exc:
        return _deny(f"the action could not be recorded ({exc}); it was refused.")
    return Decision(worst.action, worst.reason, approved_id if worst.action == "allow" else None)
