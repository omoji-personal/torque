#!/usr/bin/env python3
"""PreToolUse gate: authorize Salesforce writes by identity, never inference.

Bash mutations MUST carry --target-org (else denied — this closes the default-org,
SF_TARGET_ORG env, and `config set target-org && write` TOCTOU classes at once).
MCP mutations must carry the server's native org parameter. Both resolve to an org that
must be on the allowlist AND classify non-production at this instant.

Also gates Edit/Write of authorization artifacts: the allowlist, protected-objects,
cli-write-surface, and clean-ip.rules may not be modified by the agent — operator-present
issuance is the only path (the token principle, applied to the artifacts tokens read).
"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib

HOOK = "prod_write_gate"

# sf/sfdx mutation verbs (subset; the derived cli-write-surface refines this on real runs)
WRITE_RE = re.compile(r"\bsf(dx)?\s+.*\b("
    r"data\s+(create|update|delete|upsert|import)|"
    r"project\s+deploy|project\s+delete\s+source|force:source:(deploy|delete|push)|"
    r"apex\s+run|org\s+assign|package\s+(install|uninstall)|"
    r"data\s+bulk)", re.I)
CONFIG_MUT_RE = re.compile(r"\bsf(dx)?\s+config\s+set\b.*target-org", re.I)
TARGET_RE = re.compile(r"(?:--target-org|-o)[= ]+([^\s;|&]+)")
GATE_INPUTS = ("writable-orgs.json", "protected-objects", "cli-write-surface.json",
               "clean-ip.rules")

def handle_bash(cmd: str):
    # compound: config-mutating + write in one line ⇒ deny (TOCTOU)
    if CONFIG_MUT_RE.search(cmd) and WRITE_RE.search(cmd):
        lib.deny("compound config-set-target-org + write in one command line",
                 "compound-config-write", HOOK)
    # inline env assignment before a write ⇒ deny
    if re.match(r"\s*SF_TARGET_ORG=", cmd) and WRITE_RE.search(cmd):
        lib.deny("inline SF_TARGET_ORG assignment preceding a write", "inline-env", HOOK)
    if not WRITE_RE.search(cmd):
        lib.allow()                                   # not a write
    m = TARGET_RE.search(cmd)
    if not m:
        lib.deny("Salesforce write without an explicit --target-org", "no-target", HOOK)
    ok, reason = lib.authorize_write(m.group(1))
    if not ok:
        lib.deny(reason, "not-authorized", HOOK)
    lib.allow()

def handle_mcp(tool: str, tinput: dict):
    if not re.search(r"(deploy_metadata|create_record|update_record|delete_record|"
                     r"upsert_record|assign_permission_set|execute_anonymous_apex|"
                     r"bulk)", tool, re.I):
        lib.allow()
    target = (tinput.get("targetOrg") or tinput.get("target-org")
              or tinput.get("username") or tinput.get("usernameOrAlias"))
    if not target:
        lib.deny(f"MCP write tool {tool} without an org parameter", "mcp-no-target", HOOK)
    ok, reason = lib.authorize_write(target)
    if not ok:
        lib.deny(reason, "not-authorized", HOOK)
    lib.allow()

def handle_edit(tinput: dict):
    path = tinput.get("file_path", "")
    if any(g in path for g in GATE_INPUTS):
        lib.deny(f"agent modification of authorization artifact {os.path.basename(path)} "
                 f"is denied; use operator-present issuance", "artifact-edit", HOOK)
    lib.allow()

def main():
    ev = lib.read_event()
    tool = ev.get("tool_name", "")
    tinput = ev.get("tool_input", {}) or {}
    if tool == "Bash":
        handle_bash(tinput.get("command", ""))
    elif tool in ("Edit", "Write", "MultiEdit"):
        handle_edit(tinput)
    elif tool.startswith("mcp__"):
        handle_mcp(tool, tinput)
    lib.allow()

if __name__ == "__main__":
    main()
