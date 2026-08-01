#!/usr/bin/env python3
"""PreToolUse gate: authorize Salesforce writes by IDENTITY; protect the trust anchor.

Thin now — all Bash/MCP parsing lives in the shared, expansion-aware shellparse classifier
(audit round 10), so this gate and destructive_data_gate can never disagree about whether a
command runs sf. This gate answers one question per write: is the resolved target authorized?
Non-production ⇒ allowlist + live verdict. Production ⇒ operator override only (lib handles it).
"""
import os, sys
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import lib
    import shellparse
except Exception as _e:                                # a tampered lib/shellparse must NOT fail open
    print(f"TORQUE GATE: import failed, failing closed: {_e}", file=sys.stderr)
    sys.exit(2)

HOOK = "prod_write_gate"


def _is_sf_auth(path):
    """The sf CLI auth store (~/.sfdx, ~/.sf) holds accessToken/sfdxAuthUrl — reading it via the
    Read tool would let the agent lift a live token and bypass sf entirely (audit R11-06).
    Expansion-aware so `~/.sfd*/alias.json` is caught too (audit T12-01)."""
    pat = shellparse._abs_pattern(path)
    for d in (Path.home() / ".sfdx", Path.home() / ".sf"):
        if shellparse._pattern_reaches_dir(pat, str(d.resolve())):
            return True
    return False


def handle_bash(cmd):
    r = shellparse.analyze_bash(cmd)
    if r["deny"]:
        lib.deny(r["deny"][0], r["deny"][1], HOOK)
    for sf_args in r.get("writes", []):
        tset = set(shellparse.targets(sf_args))
        if len(tset) == 0:
            lib.deny("Salesforce write without an explicit --target-org/-o/-u "
                     "(default-org and env-target writes are refused)", "no-target", HOOK)
        if len(tset) > 1:
            lib.deny(f"ambiguous write targets {sorted(tset)} in one command",
                     "ambiguous-target", HOOK)
        ok, reason = lib.authorize_write(next(iter(tset)))
        if not ok:
            lib.deny(reason, "not-authorized", HOOK)
    lib.allow()


def handle_mcp(tool, tinput):
    r = shellparse.mcp_analyze(tool, tinput)
    if r.get("read"):
        lib.allow()
    target = r.get("write")
    if not target:
        lib.deny(f"MCP write tool {tool} without an org parameter", "mcp-no-target", HOOK)
    ok, reason = lib.authorize_write(target)
    if not ok:
        lib.deny(reason, "not-authorized", HOOK)
    lib.allow()


def _is_gate_file(path):
    return os.path.basename(path) in shellparse.PROTECTED_BASENAMES


def _tool_paths(tinput):
    """Every value in a tool payload that could be a filesystem path.

    The guards read only `file_path`, so the same Read/Edit/Write with the path under `path`,
    `target_file`, `filepath` or any other key sailed through — the protection depended on a
    key name rather than on the content. Claude Code's own tools use `file_path`, but MCP
    tools and future surfaces need not, and a guard should not rely on that (external panel,
    antigravity/gemini-3.1-pro).
    """
    out = []
    for k, v in (tinput or {}).items():
        if not isinstance(v, str) or not v:
            continue
        if k in ("content", "old_string", "new_string", "command", "prompt", "query"):
            continue
        if v.startswith(("/", "~", "./", "../")) or "/" in v:
            out.append(v)
    return out


def handle_edit(tinput):
    # every path-shaped value, not just the one key — see _tool_paths()
    path = tinput.get("file_path", "") or next(iter(_tool_paths(tinput)), "")
    if lib.is_protected_target(path) or shellparse.anchor_ref(path) or _is_gate_file(path) \
       or _is_sf_auth(path) or shellparse.sf_auth_ref(path):    # auth store too (audit TQ-F4)
        lib.deny(f"agent modification of protected file {os.path.basename(path)} is denied; "
                 "operator-present issuance only", "artifact-edit", HOOK)
    lib.allow()


def handle_read(tinput):
    # Audit R-01: the agent's Read tool must not read the signing secret or token store, or the
    # Bash secret-read guard is theater. Reading the approved-apex copy stays allowed.
    # every path-shaped value, not just the one key — see _tool_paths()
    path = tinput.get("file_path", "") or next(iter(_tool_paths(tinput)), "")
    if shellparse.anchor_ref(path):
        lib.deny("reading the trust anchor (signing secret / tokens) is denied", "anchor-read", HOOK)
    if _is_sf_auth(path):
        lib.deny("reading the sf CLI auth store (~/.sfdx, ~/.sf) is denied — it holds live "
                 "access tokens", "auth-read", HOOK)
    lib.allow()


def main():
    ev = lib.read_event()
    tool = ev.get("tool_name", "")
    tinput = ev.get("tool_input", {}) or {}
    if tool == "Bash":
        handle_bash(tinput.get("command", ""))
    elif tool in ("Edit", "Write", "MultiEdit"):
        handle_edit(tinput)
    elif tool == "Read":
        handle_read(tinput)
    elif tool.startswith("mcp__"):
        handle_mcp(tool, tinput)
    lib.allow()


if __name__ == "__main__":
    lib.run_gate(main, HOOK)
