#!/usr/bin/env python3
"""PreToolUse gate: authorize Salesforce writes by IDENTITY; protect the trust anchor.

Thin now — all Bash/MCP parsing lives in the shared, expansion-aware shellparse classifier
(audit round 10), so this gate and destructive_data_gate can never disagree about whether a
command runs sf. This gate answers one question per write: is the resolved target authorized?
Non-production ⇒ allowlist + live verdict. Production ⇒ operator override only (lib handles it).
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib
import shellparse

HOOK = "prod_write_gate"


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


def handle_edit(tinput):
    path = tinput.get("file_path", "")
    if lib.is_protected_target(path) or shellparse.anchor_ref(path) or _is_gate_file(path):
        lib.deny(f"agent modification of protected file {os.path.basename(path)} is denied; "
                 "operator-present issuance only", "artifact-edit", HOOK)
    lib.allow()


def handle_read(tinput):
    # Audit R-01: the agent's Read tool must not read the signing secret or token store, or the
    # Bash secret-read guard is theater. Reading the approved-apex copy stays allowed.
    path = tinput.get("file_path", "")
    if shellparse.anchor_ref(path):
        lib.deny("reading the trust anchor (signing secret / tokens) is denied", "anchor-read", HOOK)
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
