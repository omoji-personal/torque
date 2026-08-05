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
        return None, lib.first_line(r.stderr, r.stdout, default="sf exited non-zero")[:100]
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
    # P1-004: the mode must be one this gate knows how to enforce. Tokens minted before the mode
    # existed carry none, and are still honoured as ceilings because that is exactly what they
    # were — but an UNRECOGNISED mode is refused rather than treated as a ceiling.
    #
    # That is the whole point of naming it. If an exact-scope mode is ever built, it will bind
    # WHICH rows and not merely how many. A gate that ignored the field would accept such a token
    # and enforce the weaker guarantee, silently, while the operator believed they had approved
    # the stronger one. Refusing what it does not understand is the only safe reading of a
    # payload whose promise it cannot honour.
    mode = impact.get("mode", "impact_ceiling")
    if mode != "impact_ceiling":
        lib.deny(f"this approval declares mode {mode!r}, which this gate does not enforce. "
                 f"Refusing rather than downgrading it to a count ceiling — the token promises "
                 f"something stronger than this gate can deliver.", "impact-mode", HOOK)
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

    The MATCHING now lives in lib.protected_object_hits, because a third caller turned up —
    bin/torque-shadow, whose own protected-object refusal was dead code for want of exactly this
    function. The policy stays here; the question moved to one place, which is what
    no_divergent_twins has been asking for since the second time this was fixed.
    """
    for obj in lib.protected_object_hits(text):
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


# DELETE only, and the omission of update is a DECISION rather than an oversight.
#
# An update can be as destructive as a delete — one field wiped across every Account, a record
# at a time — so the symmetry is tempting. Three things argue against it, and the fixtures
# record all three. A record-level update is reversible: working-discipline.md requires the
# before-values in the session log, and there is nothing equivalent for a deleted row beyond the
# recycle bin. Updating an Account by Id is the single most ordinary write in Salesforce
# consulting, and gating it would put an approval in front of the daily path — the corpus of
# real commands in gate_fixtures_r17.json contains exactly such an update, and an earlier draft
# of this patch denied it. And the operator authorised deletion; extending to update on the way
# past would be a scope change nobody agreed to.
#
# If update should be covered, it is one entry in this dict plus its fixtures — deliberately
# cheap, so the decision can be revisited without re-deriving it.
_PROTECTED_RECORD_OPS = {"delete": "protected-record-delete"}


def _protected_record_mutation(sf_args):
    """A record-level delete naming an Id, on an sObject the operator declared protected.

    `classify_destructive` returns None for these DELIBERATELY: delete-by-Id is the form
    working-discipline.md mandates, and gating it for every object would kill the primitive the
    rules require. The consequence nobody had traced is that `_gate_write` returned on `not op`
    BEFORE `_shield_tokens` ran, so the protected-object floor existed only on the
    destructive-class path. The three objects an operator listed by hand could be removed one
    record at a time, unlimited and unasked, while the bulk spelling of the identical deletion
    was refused outright (M8, 2026-08-05 — twelve chained single-Id deletes, zero approvals).

    The floor is the backstop against token over-scope: bulk-delete tokens are not object-scoped,
    so a legitimately-issued one plus a lowercase name reached the protected objects (red-team
    P1-6). A backstop that the mandated spelling of delete walks past is not one.

    Returns an op NAME, so this joins the ordinary token path rather than the shield's absolute
    refusal. The capability survives with an approval attached — including the impact-bound token,
    which is the answer to doing this at volume: one approval naming a scope, not one per record.
    Refusing absolutely is the stronger floor and stays one line away; it was not taken because it
    would stop the agent deleting a TEST- Account it created itself.
    """
    sub = shellparse.subcommand(sf_args)
    f = sub[0] if sub else ""
    for verb, op in _PROTECTED_RECORD_OPS.items():
        if sub[:3] == ("data", verb, "record") or f.startswith(f"force:data:record:{verb}"):
            break
    else:
        return None
    if not shellparse.has_record_id(sf_args):
        return None                    # no Id => classify_destructive already said where-delete
    sv = shellparse.sobject_value(sf_args)
    if not sv:
        return None
    # Case-insensitive for the reason _shield_tokens is: Salesforce object names are, so
    # `--sobject account` reaches the same table as `Account`.
    if sv.strip().lower() not in {p.strip().lower() for p in lib.protected_objects()}:
        return None
    return op


def _gate_write(sf_args):
    op = shellparse.classify_destructive(sf_args)
    if not op:
        op = _protected_record_mutation(sf_args)
        if not op:
            return
    # Exactly one target, or deny — the rule its twin already had. Without it a destructive
    # command with no target looked up a token for the identity "?", and one with several
    # picked an arbitrary member of a set.
    target = lib.sole_target(shellparse.targets(sf_args), HOOK)
    _, oid, _ = lib.classify(target)
    orgid = oid or "?"
    if op in _PROTECTED_RECORD_OPS.values():
        # Deliberately AHEAD of _shield_tokens: the shield refuses absolutely and no token clears
        # it, which is right for a bulk delete and wrong here — this is the spelling the rules
        # mandate, so it gets an approval path rather than a wall.
        _need_token(orgid, op)
        return
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
    # See prod_write_gate.handle_bash: the destructive decision moves to the shim with the rest
    # of it, and lands on `_gate_write` there via handle_argv — the same function, on argv the
    # kernel resolved rather than a string this layer had to reconstruct.
    if r.get("defer"):
        lib.audit("DEFER", f"[{HOOK}:{r['defer'][1]}] {r['defer'][0]}")
    for sf_args in r.get("writes", []):
        _gate_write(sf_args)
    lib.allow()



def handle_argv(sf_args):
    """Exec-time twin of handle_bash: same _gate_write, argv straight from the kernel.

    See prod_write_gate.handle_argv for why the shim does not hand over a reconstructed command
    string. The destructive half matters more for that reason, not less: this gate reads the
    positional token stream to shield protected sObjects and digests an Apex file, and both of
    those are exactly the arguments most likely to carry punctuation a shell parser has an
    opinion about.
    """
    lib.remember_command("[shim] sf " + " ".join(sf_args))
    if shellparse.is_read(sf_args):
        lib.allow()
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
    elif tool == "SfArgv":
        handle_argv(list(tinput.get("argv", [])))
    elif shellparse.is_mcp_tool(tool):
        handle_mcp(tool, tinput)
    lib.allow()


if __name__ == "__main__":
    lib.run_gate(main, HOOK)
