#!/usr/bin/env python3
"""PreToolUse gate: destructive operations require an operator-present approval token.

Destructive classes — bulk/hard delete, WHERE-bounded-but-not-record-id delete/update,
destructive metadata deploy (--pre/--post-destructive-changes, project delete source),
and anonymous Apex — each require a single-use HMAC token an agent cannot mint. Enforced on
BOTH the Bash surface and the MCP surface (an MCP execute_anonymous_apex bypassed this gate
entirely before — audit K-8/CLAUDE-4). Protected sObjects are shielded on any org.

Anonymous Apex additionally must run from the OPERATOR-APPROVED IMMUTABLE COPY at
~/.torque/approved/<digest>.apex — not an agent-writable path that can be swapped between
the hook's digest check and sf's read (TOCTOU, audit CODEX-6). torque-approve writes that
copy; the agent cannot (anchor writes are Bash-denied). The gate re-digests the copy itself.
"""
import hashlib, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib
from pathlib import Path

HOOK = "destructive_data_gate"

# --- Bash patterns --------------------------------------------------------
BULK_DELETE = re.compile(r"\bsf(dx)?\s+data\s+(delete\s+bulk|bulk\s+delete)|--hard-delete\b", re.I)
BULK_UPDATE = re.compile(r"\bsf(dx)?\s+data\s+(update\s+bulk|bulk\s+upsert|upsert\s+bulk|import)\b", re.I)
RECORD_DELETE = re.compile(r"\bsf(dx)?\s+(data\s+delete\s+record|force:data:record:delete)\b", re.I)
RECORD_UPDATE = re.compile(r"\bsf(dx)?\s+(data\s+update\s+record|force:data:record:update)\b", re.I)
# A record-id is a bound of exactly one. A bare --where is NOT a safe bound: "Id != null" is a
# tautology that touches every row (audit K-7). Only --record-id/-i exempts from a token.
RECORD_ID = re.compile(r"(--record-id|-i)\b", re.I)
DESTRUCTIVE_META = re.compile(
    r"destructiveChanges|\bsf(dx)?\s+project\s+delete\s+source\b|force:source:delete|"
    r"force:mdapi:deploy.*destructiveChanges|--(pre|post)-destructive-changes\b", re.I)
APEX = re.compile(r"\bsf(dx)?\s+(.*\s)?apex\s+run\b|force:apex:execute", re.I)
FILE_FLAG = re.compile(r"(?:--file|-f)[=\s]+([^\s;|&]+)")
TARGET_RE = re.compile(r"(?:--target-org|--targetusername|-o|-u)[=\s]+([^\s;|&]+)")


def _need_token(orgid, op_class, digest=""):
    if lib.consume_token(orgid, op_class, digest):
        lib.audit("ALLOW", f"[{HOOK}] token accepted for {op_class} on {orgid}")
        lib.allow()
    lib.deny(f"{op_class} requires operator-present approval (bin/torque-approve). "
             "An agent cannot mint an HMAC-signed token.", op_class, HOOK)


def _shield(text, orgid):
    for obj in lib.protected_objects():
        if re.search(rf"\b{re.escape(obj)}\b", text):
            lib.deny(f"operation targets protected sObject {obj}", "protected-object", HOOK)


def _apex_digest_from_file(path_str):
    """The apex file MUST be the operator-approved immutable copy under the anchor; re-digest
    THAT file (agent cannot alter it) so a post-check swap is impossible (TOCTOU CODEX-6)."""
    try:
        rp = Path(os.path.expanduser(path_str)).resolve()
    except Exception:
        lib.deny("apex --file path unresolvable", "apex-path", HOOK)
    approved = lib.APPROVED.resolve()
    if not (str(rp) == str(approved) or str(rp).startswith(str(approved) + os.sep)):
        lib.deny("anonymous Apex --file must be the operator-approved copy at "
                 "~/.torque/approved/<digest>.apex (agent-writable paths are TOCTOU-unsafe)",
                 "apex-not-approved", HOOK)
    try:
        body = rp.read_bytes()
    except Exception:
        lib.deny("approved apex copy unreadable", "apex-unreadable", HOOK)
    digest = hashlib.sha256(body).hexdigest()[:16]
    if rp.stem != digest:                             # filename encodes the content digest
        lib.deny("approved apex digest mismatch (copy was altered)", "apex-digest", HOOK)
    return digest, body.decode("utf-8", "replace")


def handle_bash(cmd: str):
    m = TARGET_RE.search(cmd)
    orgid = None
    if m:
        _, orgid, _ = lib.classify(m.group(1))
    orgid = orgid or "?"

    if BULK_DELETE.search(cmd) or DESTRUCTIVE_META.search(cmd):
        _shield(cmd, orgid)

    if APEX.search(cmd):
        fm = FILE_FLAG.search(cmd)
        if not fm:
            lib.deny("anonymous Apex without --file (stdin/inline cannot be digest-bound)",
                     "apex-stdin", HOOK)
        digest, body = _apex_digest_from_file(fm.group(1))
        _shield(body, orgid)
        _need_token(orgid, "apex", digest)
    if BULK_DELETE.search(cmd):
        _need_token(orgid, "bulk-delete")
    if BULK_UPDATE.search(cmd):
        _need_token(orgid, "bulk-write")
    if RECORD_DELETE.search(cmd) and not RECORD_ID.search(cmd):
        _need_token(orgid, "where-delete")
    if RECORD_UPDATE.search(cmd) and not RECORD_ID.search(cmd):
        _need_token(orgid, "where-update")
    if DESTRUCTIVE_META.search(cmd):
        _need_token(orgid, "destructive-metadata")
    lib.allow()


def handle_mcp(tool: str, tinput: dict):
    low = tool.lower()
    target = (tinput.get("targetOrg") or tinput.get("target-org")
              or tinput.get("username") or tinput.get("usernameOrAlias"))
    orgid = "?"
    if target:
        _, oid, _ = lib.classify(target)
        orgid = oid or "?"

    if "execute_anonymous_apex" in low or low.endswith("__anonymous_apex"):
        body = (tinput.get("apexCode") or tinput.get("apex") or tinput.get("code") or "")
        _shield(body, orgid)
        digest = hashlib.sha256(body.encode()).hexdigest()[:16]
        _need_token(orgid, "apex", digest)
    if "bulk" in low and "delete" in low:
        _shield(str(tinput), orgid); _need_token(orgid, "bulk-delete")
    if "bulk" in low:
        _need_token(orgid, "bulk-write")
    if low.endswith("delete_record"):
        if not (tinput.get("recordId") or tinput.get("id") or tinput.get("record-id")):
            _need_token(orgid, "where-delete")
    if "deploy_metadata" in low:
        if tinput.get("preDestructiveChanges") or tinput.get("postDestructiveChanges") \
           or tinput.get("pre-destructive-changes") or tinput.get("post-destructive-changes"):
            _shield(str(tinput), orgid); _need_token(orgid, "destructive-metadata")
    lib.allow()


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
