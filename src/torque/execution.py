"""Execution evidence for approved calls: a PostToolUse (or PostToolUseFailure)
event of the same gate hook records `approval_executed` in the change record,
linked to the `approval_consume` event by tool_use_id. Consuming an approval says
the gate let a call through; this says the call ran and how it ended. It never
blocks: a hook after the call cannot undo it.

Outcome rules (host facts from D0):
- PostToolUseFailure fires instead of PostToolUse for a nonzero Bash exit; its
  `error` starts `Exit code N` on its first line. `failed` with N (None when the
  first line carries no code), or `interrupted` when `is_interrupt` is true.
- PostToolUse for Bash fires only for exit 0 in the foreground, but a call that
  hits its own timeout is moved to a background task and still logs PostToolUse
  (`backgroundTaskId`, `timedOutAfterMs`, `interrupted` false, no exit code). So
  `succeeded` with 0 needs a clear foreground signal: a `tool_response` object
  whose `interrupted` is exactly false, with no background or timeout key and no
  `run_in_background` in the input. Anything else is `unknown` with None, never
  `succeeded`.
- A non-Bash tool (MCP, browser) has no exit status: its PostToolUse is
  `succeeded` with None, unless its response carries a background key."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

POST_EVENTS = ("PostToolUse", "PostToolUseFailure")
# D0: the first line of PostToolUseFailure's `error` is `Exit code N`.
_EXIT = re.compile(r"Exit code (\d+)\s*$")
_BACKGROUND_KEYS = ("backgroundTaskId", "timedOutAfterMs", "backgroundedByUser")


def _backgrounded(event: dict, response) -> bool:
    tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    if tool_input.get("run_in_background"):
        return True
    if not isinstance(response, dict):
        return False
    return any(key in response for key in _BACKGROUND_KEYS) or \
        any("background" in str(key).lower() for key in response)


def outcome(event: dict) -> tuple[str, int | None]:
    """(`succeeded`, `failed`, `interrupted` or `unknown`, exit status or None)."""
    if event.get("hook_event_name") == "PostToolUseFailure":
        if event.get("is_interrupt") is True:
            return "interrupted", None
        first = str(event.get("error") or "").split("\n", 1)[0]
        match = _EXIT.match(first)
        return "failed", int(match.group(1)) if match else None
    response = event.get("tool_response")
    if isinstance(response, dict) and response.get("interrupted") is True:
        return "interrupted", None
    if _backgrounded(event, response):
        return "unknown", None
    if isinstance(response, dict):
        code = response.get("exitCode", response.get("exit_code"))
        if type(code) is int:
            return ("succeeded" if code == 0 else "failed"), code
    if event.get("tool_name") == "Bash":
        if isinstance(response, dict) and response.get("interrupted") is False \
                and "exitCode" not in response and "exit_code" not in response:
            return "succeeded", 0
        return "unknown", None
    return "succeeded", None


def _marker_approval(dirs: dict, tool_use_id: str, session_id) -> str | None:
    """The approval whose consume marker names this call (command and MCP approvals,
    and a browser window's first use)."""
    from .approval import _read, _valid_id
    for marker in sorted(dirs["consumed"].glob("apr-*")):
        if not _valid_id(marker.name, "apr-"):
            continue
        used = _read(marker)
        if used and used.get("tool_use_id") == tool_use_id and used.get("session_id") == session_id:
            return marker.name
    return None


def _browser_approval(dirs: dict, tool_use_id: str, session_id) -> str | None:
    """The browser window the gate logged this call under (every use after the first)."""
    try:
        lines = (dirs["base"] / "activity.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("action") == "browser" and row.get("tool_use_id") == tool_use_id \
                and row.get("session_id") == session_id:
            return row.get("approval_id")
    return None


def _record(root: Path, slug: str, event: dict) -> None:
    from . import approval, changes
    tool_use_id, session_id = event.get("tool_use_id"), event.get("session_id")
    if not isinstance(tool_use_id, str) or not tool_use_id:
        return
    dirs = approval._dirs(root, slug, create=False)
    if not dirs["consumed"].is_dir():
        return
    approval_id = _marker_approval(dirs, tool_use_id, session_id)
    browser = approval_id is None
    if browser:
        approval_id = _browser_approval(dirs, tool_use_id, session_id)
    if not approval._valid_id(approval_id, "apr-"):
        return
    record = approval._read(dirs["granted"] / f"{approval_id}.json")
    if not record or record.get("id") != approval_id or (browser and record.get("kind") != "browser"):
        return
    events = changes.get_change(root, slug, record.get("change"))["events"]
    consumes = [e for e in events if e["kind"] == "approval_consume" and e.get("approval_id") == approval_id]
    # The gate's own consume event must exist: for this exact call, or, for a later
    # action in a browser window, the window's single first-use consumption.
    consume = next((e for e in consumes if e.get("tool_use_id") == tool_use_id
                    and e.get("session_id") == session_id), None)
    if consume is None and browser and len(consumes) == 1:
        consume = consumes[0]
    if consume is None:
        return
    if any(e["kind"] == "approval_executed" and e.get("approval_id") == approval_id
           and e.get("tool_use_id") == tool_use_id for e in events):
        return
    state, code = outcome(event)
    changes.append_approval_event(root, slug, record["change"], "approval_executed", {
        "approval_id": approval_id, "request_id": record.get("request_id"), "tool_use_id": tool_use_id,
        "session_id": session_id, "outcome": state, "exit_status": code, "consume_event_id": consume.get("id"),
        "approver_kind": record.get("approver_kind"), "org_alias": record.get("org_alias"),
        "command": record.get("command")})


def handle(event: dict, environ) -> int:
    """Record the execution of an approved call. Always 0: the call already ran."""
    from . import gate, launch
    try:
        with gate._call_budget():
            tool_input = event.get("tool_input")
            if tool_input is None:
                tool_input = {}
            if not isinstance(tool_input, dict) or not isinstance(event.get("tool_name"), str):
                return 0
            cwd = Path(os.path.realpath(str(event.get("cwd") or ".")))
            for root in gate._connected_workspaces(cwd, tool_input):
                env = launch.bound_env(environ, root)
                if env.get("TORQUE_CLIENT"):
                    _record(Path(root), env["TORQUE_CLIENT"], event)
    except Exception as exc:  # evidence is best effort; the call already ran
        print(f"Connected mode: the execution record could not be written ({exc}).", file=sys.stderr)
    return 0
