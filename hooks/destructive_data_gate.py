#!/usr/bin/env python3
"""PreToolUse gate: destructive operations require an operator-present approval token.

Shares the shellparse classifier with prod_write_gate (audit round 10 — the destructive side
is NO LONGER raw-text regex, which quote/space/indirection trivially evaded). Destructive
classes (bulk/hard delete, WHERE-not-record-id delete/update, destructive-metadata deploy,
anonymous Apex) each need a single-use HMAC token an agent cannot mint. Protected sObjects are
shielded over the SHLEX-DECODED token stream. Anonymous Apex must run from the operator-
approved immutable copy at ~/.torque/approved/<digest>.apex (TOCTOU-safe).
"""
import json, os, sys, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import lib
    import shellparse
except Exception as _e:
    print(f"TORQUE GATE: import failed, failing closed: {_e}", file=sys.stderr)
    sys.exit(2)
from pathlib import Path

HOOK = "destructive_data_gate"


def _live_count(target, sobject, where):
    """One COUNT, read-only. (count, reason) — count is None when it cannot be established.

    The first version swallowed the reason, and the first thing it swallowed was a NameError
    from an unimported `json`. The gate then reported "the scope could not be re-established",
    which is true, actionable-sounding, and would have sent someone to look at the org.
    """
    q = f"SELECT COUNT() FROM {sobject}" + (f" WHERE {where}" if where else "")
    try:
        r = lib._sf("data", "query", "--target-org", target, "--json", "--query", q)
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:80]}"
    if r.returncode != 0:
        # `or` picks the first TRUTHY value, and whitespace-only stderr is truthy — then
        # .strip().splitlines() is empty and [0] raises IndexError. The gate reported "crashed"
        # instead of "could not count", i.e. it lost the reason while constructing the reason
        # (codex/gpt-5.6-sol, round 5). Strip first, then choose.
        _why = ((r.stderr or "").strip() or (r.stdout or "").strip() or "sf exited non-zero")
        return None, _why.splitlines()[0][:100]
    try:
        return json.loads(r.stdout)["result"]["totalSize"], ""
    except Exception as e:
        return None, f"unparseable count response ({type(e).__name__})"


def _need_impact_token(orgid, op, target, sobject, where):
    """Spend a token that was approved for a specific scope, and only within that scope.

    The operator saw a number when they approved. Data moves; criteria that matched seven rows
    an hour ago can match seven thousand now. So the count is re-established here and the token
    is refused if the operation grew — a bound the industry's per-command-string approvals
    cannot express, because a command string says nothing about how much it touches.

    If the count cannot be established, this DENIES. An impact-bound token whose impact is
    unknown is not a token.
    """
    digest = lib.impact_digest(sobject, where)
    payload = lib.consume_token_payload(orgid, op, digest)
    if payload is None:
        return False
    impact = payload.get("impact") or {}
    approved = impact.get("scope")
    if approved is None:
        # A token bearing this digest but no recorded scope is not an impact-bound approval.
        # Honouring it here skipped the count entirely — the one thing this path exists to do
        # (release panel round 2, codex/gpt-5.6-sol). Ordinary tokens live on the empty-digest
        # path and are unaffected.
        return False
    live, why = _live_count(target, sobject, where)
    if live is None:
        lib.deny(f"an impact-bound approval was spent but the scope could not be re-established "
                 f"on {target} — refusing rather than assuming it is unchanged [{why}]",
                 "impact-unverifiable", HOOK)
    if live > approved:
        lib.deny(f"approved for {approved} {sobject} record(s), the criteria now match {live}. "
                 f"The operation grew after approval; re-approve with the current scope.",
                 "impact-drift", HOOK)
    # This read `"PROD-WRITE" if False else "ALLOW"` — dead scaffolding left in a live audit
    # call, so the branch it appeared to distinguish could never be taken (release panel round 3).
    # The distinction was never this function's to make: production is adjudicated by
    # prod_write_gate, and by the time an impact token is spent that decision is behind us.
    lib.audit("ALLOW",
              f"impact-bound {op} on {orgid}: approved {approved}, live {live}")
    return True


def _need_token(orgid, op, digest=""):
    # returns on success (does NOT allow()/exit) so EVERY write in a compound command is checked
    # before the gate allows — one token must not authorize two ops in `A && B` (audit TQ-003)
    if lib.consume_token(orgid, op, digest):
        lib.audit("ALLOW", f"[{HOOK}] token accepted for {op} on {orgid}")
        return
    lib.deny(f"{op} requires operator-present approval. An agent cannot mint an "
             f"HMAC-signed token. Run: {lib.approve_cmd(orgid or '<org>', op)}", op, HOOK)


def _shield_tokens(tokens, orgid):        # orgid: unused, deliberately — see below
    # The shield is NOT org-scoped, and that is the stronger position: a protected object is
    # refused on every org, including a developer one, because "it was only the sandbox" is how
    # the habit forms. The parameter survives from an org-aware design that was never built; it
    # is kept so the two shields share a signature, and named here so a reader does not assume
    # the scoping exists.
    # Salesforce object names are case-insensitive, so `--sobject account` reached the same table
    # as `Account` while slipping past an exact-set membership test. Bulk-delete tokens are not
    # object-scoped, so one legitimately-issued token plus a lowercase name deleted the very
    # objects the shield exists to protect (red-team P1-6).
    prot = {p.strip().lower() for p in lib.protected_objects()}
    for t in tokens:
        if t.strip().lower() in prot:
            lib.deny(f"operation targets protected sObject {t}", "protected-object", HOOK)


def _shield_text(text, orgid):
    """The protected-object floor, applied to an Apex body. `orgid` is unused — the floor is not
    org-scoped; see _shield_tokens.

    Apex sObject names are case-insensitive, so `delete [SELECT Id FROM account]` reaches the
    same table as `Account`. _shield_tokens learned this from an earlier red-team finding and
    was fixed; this twin was not, so the floor held on the CLI path and not on the Apex path —
    which is the path that runs arbitrary DML (release panel, codex/gpt-5.6-sol).

    A fix applied to one call site and not its twin is the failure mode this pair now guards
    against: shield_is_case_insensitive exercises BOTH.
    """
    import re
    for obj in lib.protected_objects():
        if re.search(rf"\b{re.escape(obj)}\b", text, re.IGNORECASE):
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
    # Exactly one target, or deny — the rule its twin already had. Without it a destructive
    # command with no target looked up a token for the identity "?", and one with several
    # picked an arbitrary member of a set.
    target = lib.sole_target(shellparse.targets(sf_args), HOOK)
    _, oid, _ = lib.classify(target)
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
        return
    # An impact-bound token is tried FIRST, and only when the command carries the two things
    # the approval was computed from. If none exists, this falls through to the ordinary token
    # path unchanged — impact binding is an option the operator takes, not a new obstacle.
    where = shellparse.flag_value(sf_args, "--where", "-w")
    if sv and _need_impact_token(orgid, op, target, sv, where):
        return
    _need_token(orgid, op)


def handle_bash(cmd):
    lib.remember_command(cmd)
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
    elif shellparse.is_mcp_tool(tool):
        handle_mcp(tool, tinput)
    lib.allow()


if __name__ == "__main__":
    lib.run_gate(main, HOOK)
