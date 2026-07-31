#!/usr/bin/env python3
"""PreToolUse gate: destructive operations require an operator-present approval token.

Shares the shellparse classifier with prod_write_gate (audit round 10 — the destructive side
is NO LONGER raw-text regex, which quote/space/indirection trivially evaded). Destructive
classes (bulk/hard delete, WHERE-not-record-id delete/update, destructive-metadata deploy,
anonymous Apex) each need a single-use HMAC token an agent cannot mint. Protected sObjects are
shielded over the SHLEX-DECODED token stream. Anonymous Apex must run from the operator-
approved immutable copy at ~/.torque/approved/<digest>.apex (TOCTOU-safe).
"""
import os, sys, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import lib
    import shellparse
except Exception as _e:
    print(f"TORQUE GATE: import failed, failing closed: {_e}", file=sys.stderr)
    sys.exit(2)
from pathlib import Path

HOOK = "destructive_data_gate"


def _need_token(orgid, op, digest=""):
    if lib.consume_token(orgid, op, digest):
        lib.audit("ALLOW", f"[{HOOK}] token accepted for {op} on {orgid}")
        lib.allow()
    lib.deny(f"{op} requires operator-present approval (bin/torque-approve). "
             "An agent cannot mint an HMAC-signed token.", op, HOOK)


def _shield_tokens(tokens, orgid):
    prot = lib.protected_objects()
    for t in tokens:
        if t in prot:
            lib.deny(f"operation targets protected sObject {t}", "protected-object", HOOK)


def _shield_text(text, orgid):
    import re
    for obj in lib.protected_objects():
        if re.search(rf"\b{re.escape(obj)}\b", text):
            lib.deny(f"operation references protected sObject {obj}", "protected-object", HOOK)


def _apex_digest(path_str):
    try:
        rp = Path(os.path.expanduser(path_str)).resolve()
    except Exception:
        lib.deny("apex --file path unresolvable", "apex-path", HOOK)
    approved = lib.APPROVED.resolve()
    if not (str(rp) == str(approved) or str(rp).startswith(str(approved) + os.sep)):
        lib.deny("anonymous Apex --file must be the operator-approved copy at "
                 "~/.torque/approved/<digest>.apex", "apex-not-approved", HOOK)
    try:
        body = rp.read_bytes()
    except Exception:
        lib.deny("approved apex copy unreadable", "apex-unreadable", HOOK)
    digest = hashlib.sha256(body).hexdigest()[:16]
    if rp.stem != digest:
        lib.deny("approved apex digest mismatch (copy altered)", "apex-digest", HOOK)
    return digest, body.decode("utf-8", "replace")


def _gate_write(sf_args):
    op = shellparse.classify_destructive(sf_args)
    if not op:
        return
    orgid = "?"
    tset = set(shellparse.targets(sf_args))
    if tset:
        _, oid, _ = lib.classify(next(iter(tset)))
        orgid = oid or "?"
    # protected-object shield over the SHLEX-DECODED positional token stream (audit R10-R3),
    # PLUS the parsed --sobject/-s value incl. the equals form --sobject=X (audit R11-07)
    shield_toks = [a for a in sf_args if not a.startswith("-")]
    sv = shellparse.sobject_value(sf_args)
    if sv:
        shield_toks.append(sv)
    _shield_tokens(shield_toks, orgid)
    if op == "apex":
        fpath = shellparse.file_value(sf_args)
        if not fpath:
            lib.deny("anonymous Apex without --file (stdin/inline cannot be digest-bound)",
                     "apex-stdin", HOOK)
        digest, body = _apex_digest(fpath)
        _shield_text(body, orgid)
        _need_token(orgid, "apex", digest)
    _need_token(orgid, op)


def handle_bash(cmd):
    r = shellparse.analyze_bash(cmd)
    if r["deny"]:
        lib.deny(r["deny"][0], r["deny"][1], HOOK)
    for sf_args in r.get("writes", []):
        _gate_write(sf_args)
    lib.allow()


def handle_mcp(tool, tinput):
    r = shellparse.mcp_analyze(tool, tinput)
    if r.get("read"):
        lib.allow()
    dest = r.get("destructive")
    if not dest:
        lib.allow()
    op, digest, body = dest
    if op == "apex":
        # inline MCP apex cannot be bound to an operator-reviewed immutable copy — route it
        # through `sf apex run --file ~/.torque/approved/<digest>.apex` instead.
        lib.deny("anonymous Apex via MCP is not approvable; use sf apex run --file with an "
                 "operator-approved copy", "apex-mcp", HOOK)
    target = r.get("write")
    orgid = "?"
    if target:
        _, oid, _ = lib.classify(target)
        orgid = oid or "?"
    _shield_text(body or "", orgid)
    _need_token(orgid, op, "")


def main():
    ev = lib.read_event()
    tool = ev.get("tool_name", "")
    tinput = ev.get("tool_input", {}) or {}
    if tool == "Bash":
        handle_bash(tinput.get("command", ""))
    elif tool.startswith("mcp__"):
        handle_mcp(tool, tinput)
    lib.allow()


if __name__ == "__main__":
    lib.run_gate(main, HOOK)
