#!/usr/bin/env python3
"""PreToolUse: refuse an edit that would leave a hook source unparseable.

WHY THIS EXISTS. On 2026-08-04, inside a maintainer window, an edit put docstring prose after
the closing triple-quote in hooks/lib.py. One syntax error, and both gates — which import lib —
failed closed on every tool call. That is correct behaviour for a gate that cannot evaluate, and
it meant the agent could not run Bash, could not read, and could not repair the file it had just
broken. Only the operator could clear it. The window grants edit rights, not recovery rights.

So the fix is PREVENTION, at the only moment it can work: before the write lands. PreToolUse
receives the whole payload, so the resulting text can be computed and parsed BEFORE it reaches
disk. A hook source that would not import never gets written.

DELIBERATELY DEPENDENCY-FREE. It imports nothing from lib, and nothing from the rest of this
repo. A guard against "lib.py is broken" that imports lib.py is not a guard. Standard library
only, no I/O beyond reading the file being edited.

DELIBERATELY FAIL-OPEN. This is the one hook here that must allow on doubt, and the reasoning is
specific rather than a shrug: it is not an authorization control and it defends no boundary.
prod_write_gate and destructive_data_gate decide what may be edited, and they fail CLOSED. This
only asks "will the result parse". If it cannot tell — unknown tool shape, unreadable file,
anything unexpected — the right answer is to get out of the way and let the real gates rule. A
fail-closed bug here would reproduce the exact lockout it exists to prevent, from a hook nobody
would think to suspect.

Scope is hooks/*.py only. Breaking those disables enforcement and locks the session out;
breaking any other file is an ordinary mistake with an ordinary fix.
"""
import ast
import json
import os
import sys

HOOK = "syntax_guard"


def _deny(msg):
    print(f"TORQUE GATE DENY [{HOOK}] {msg}", file=sys.stderr)
    sys.exit(2)


def _guarded(path):
    """Only files whose breakage takes the gates down with them."""
    try:
        rp = os.path.realpath(os.path.expanduser(path))
    except Exception:
        return False
    if not rp.endswith(".py"):
        return False
    parts = rp.replace("\\", "/").split("/")
    return "hooks" in parts


def _result_text(tool, ti, path):
    """The text the file WOULD hold after this call, or None if it cannot be determined."""
    if tool == "Write":
        return ti.get("content")
    try:
        with open(path, "r", encoding="utf-8") as f:
            current = f.read()
    except OSError:
        return None
    if tool == "Edit":
        old, new = ti.get("old_string"), ti.get("new_string")
        if old is None or new is None or old not in current:
            return None                       # not applicable; the tool will reject it anyway
        return current.replace(old, new) if ti.get("replace_all") else current.replace(old, new, 1)
    if tool == "MultiEdit":
        for e in ti.get("edits") or []:
            old, new = e.get("old_string"), e.get("new_string")
            if old is None or new is None or old not in current:
                return None
            current = current.replace(old, new) if e.get("replace_all") \
                else current.replace(old, new, 1)
        return current
    return None


def main():
    try:
        ev = json.load(sys.stdin)
        tool = ev.get("tool_name", "")
        if tool not in ("Edit", "Write", "MultiEdit"):
            return
        ti = ev.get("tool_input", {}) or {}
        path = ti.get("file_path", "")
        if not path or not _guarded(path):
            return
        text = _result_text(tool, ti, path)
        if text is None:
            return
    except Exception:
        return                                 # see DELIBERATELY FAIL-OPEN above

    try:
        ast.parse(text)
    except SyntaxError as e:
        _deny(f"this edit would leave {os.path.basename(path)} unparseable "
              f"(line {e.lineno}: {e.msg}). Both gates import from hooks/, so writing it would "
              f"fail them closed on EVERY tool call — including the one needed to undo it. "
              f"Refused before it lands; fix the edit and retry.")


if __name__ == "__main__":
    main()
    sys.exit(0)
