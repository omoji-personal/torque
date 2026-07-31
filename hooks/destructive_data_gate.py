#!/usr/bin/env python3
"""PreToolUse gate: block destructive operations regardless of org type.

Bulk/hard delete, unbounded (WHERE-less) update, destructive metadata (destructiveChanges,
project delete source, --destructive-changes-pre/post), and anonymous Apex all require an
operator-present approval token — which an agent cannot mint. Anonymous Apex additionally
requires --file (stdin cannot be digest-bound). Protected sObjects are shielded from
delete/truncate shapes on any org.
"""
import hashlib, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib

HOOK = "destructive_data_gate"
BULK_DELETE = re.compile(r"\bsf(dx)?\s+data\s+(delete\s+bulk|delete.*--hard-delete|bulk\s+delete)", re.I)
WHERELESS_UPDATE = re.compile(r"\bsf(dx)?\s+data\s+update", re.I)
BOUNDED = re.compile(r"--where\b|WHERE\s|--record-id\b|-i\b", re.I)  # a record-id is a bound of 1
DESTRUCTIVE_META = re.compile(r"destructiveChanges|project\s+delete\s+source|force:source:delete|--destructive-changes-(pre|post)", re.I)
APEX = re.compile(r"\bsf(dx)?\s+.*apex\s+run\b|force:apex:execute", re.I)
FILE_FLAG = re.compile(r"(?:--file|-f)[= ]+([^\s;|&]+)")
TARGET_RE = re.compile(r"(?:--target-org|-o)[= ]+([^\s;|&]+)")

def _op_needs_token(cmd, orgid, op_class, digest=""):
    if lib.consume_token(orgid, op_class, digest):
        lib.audit("ALLOW", f"[{HOOK}] token accepted for {op_class} on {orgid}")
        lib.allow()
    lib.deny(f"{op_class} requires operator-present approval (torque approve). "
             f"An agent cannot mint one.", op_class, HOOK)

def handle_bash(cmd: str):
    m = TARGET_RE.search(cmd)
    target = m.group(1) if m else None
    orgid = None
    if target:
        _, orgid, _ = lib.classify(target)

    # protected-object shield (any org, even without a token path)
    if (BULK_DELETE.search(cmd) or DESTRUCTIVE_META.search(cmd)):
        for obj in lib.protected_objects():
            if re.search(rf"\b{re.escape(obj)}\b", cmd):
                lib.deny(f"operation targets protected sObject {obj}", "protected-object", HOOK)

    if APEX.search(cmd):
        fm = FILE_FLAG.search(cmd)
        if not fm:
            lib.deny("anonymous Apex without --file (stdin cannot be digest-bound)",
                     "apex-stdin", HOOK)
        try:
            body = (lib.TORQUE_HOME / fm.group(1)).read_bytes() if not os.path.isabs(fm.group(1)) \
                   else open(fm.group(1), "rb").read()
        except Exception:
            body = fm.group(1).encode()
        digest = hashlib.sha256(body).hexdigest()[:16]
        _op_needs_token(cmd, orgid or "?", "apex", digest)

    if BULK_DELETE.search(cmd):
        _op_needs_token(cmd, orgid or "?", "bulk-delete")
    if WHERELESS_UPDATE.search(cmd) and not BOUNDED.search(cmd):
        _op_needs_token(cmd, orgid or "?", "unbounded-update")
    if DESTRUCTIVE_META.search(cmd):
        _op_needs_token(cmd, orgid or "?", "destructive-metadata")
    lib.allow()

def main():
    ev = lib.read_event()
    if ev.get("tool_name") == "Bash":
        handle_bash(ev.get("tool_input", {}).get("command", ""))
    lib.allow()

if __name__ == "__main__":
    main()
