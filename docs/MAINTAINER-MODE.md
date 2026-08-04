# Maintainer mode — developing Torque without turning Torque off

**Status: designed, not applied.** Every file it touches is protected, which is the problem it
solves, so the first application has to be the operator's. After that it is self-sustaining.

## What is actually blocked

Precisely two things, and they are worth separating because only one of them is safe to relax:

1. **Editing Torque's own source.** `prod_write_gate.handle_edit` denies any Edit/Write naming a
   protected path — `hooks/`, `bin/`, `.claude/`, `harness/checks/`, `knowledge/`, `local/orgs/`,
   plus protected basenames (`validate.py`, `lib.py`, `shellparse.py`, the two gates,
   `torque-approve`, `settings.json`, `writable-orgs.json`, …) wherever they live. There is no
   token check in that path: the deny is unconditional. 19 of the 25 items in the August punch
   list are blocked on this, including all of group A.
2. **Running the harness against an org.** `python3 harness/validate.py --profile capability
   --target-org sf-coffee` is refused as "Salesforce operation via interpreter" — correctly, by
   the rule that stops an agent laundering `sf` through a script. It also stops the capability
   and release profiles running from an agent session at all.

Neither is a bug. Both follow from rules that exist for good reasons, and both were verified
live rather than inferred.

## Why the obvious answers do not work

**An environment variable (`TORQUE_DEV=1`) is worthless.** The agent composes its own Bash
commands and therefore its own environment. Any switch the agent can set is a switch the agent
can set.

**A file in the repo is worthless for the same reason,** and circular besides: to protect the
flag you would put it behind the gate that the flag exists to open.

**An ungated clone is worse than it looks.** `org-safety.md` notes that a session started
elsewhere loads no gates, so a second checkout with its hook registration stripped would let an
agent edit freely. It would also let that agent reach every authenticated org on this machine —
and on this machine that currently includes `REDACTED-ORG`, `REDACTED-ORG`, `REDACTED-ORG`, `REDACTED-ORG`,
`REDACTED-ORG`, `MAP`, `REDACTED-ORG`, `REDACTED-ORG`, `REDACTED-ORG`, `REDACTED-ORG`. Those are real legal-aid
production orgs holding real client data. An ungated workspace on this laptop is not a
development convenience; it is the exact configuration Layer 1 exists to prevent, and it trades a
file-editing inconvenience for an unbounded org risk.

## The design

The only state an agent cannot forge is state inside the trust anchor (`~/.torque`), which its
Bash, Read, Write and Edit tools cannot reach. So the grant lives there, is HMAC-signed with the
same secret as every approval token, and is minted only from a real login TTY clear of the agent
process — the presence proof `bin/torque-approve` already implements in `lib.operator_present()`.

It mirrors the production **session window** (`_prod_session_valid`) rather than the single-use
token: a time-boxed grant, not a one-shot, revoked by deleting the file.

```
torque approve --maintainer 90        # mint a 90-minute maintainer window
torque approve --end-maintainer       # revoke it
```

### The split that makes it safe

**The grant unlocks files. It does not unlock orgs. Not one line of org authorization consults
it.**

That split is the whole design, and it costs nothing: editing `lib.py` does not require writing
to `REDACTED-ORG`. Under an active grant, a production write is still denied, a destructive operation
still needs its own impact-bound token, live classification still runs at write time, and the
allowlist still governs. What changes is *who may edit Torque's source* — and only that.

Two things the grant explicitly never covers:

- **The trust anchor itself.** A grant that could rewrite `~/.torque/secret` or the token store
  could mint and extend itself, and the presence proof would become decorative. `anchor_ref` and
  the auth-store guard are checked *before* the grant and always win.
- **The sf CLI auth store.** Same reasoning; a grant is for source, not credentials.

### For the harness

The grant also permits `harness/validate.py` to run with `--target-org`, and this is a narrower
relaxation than it sounds: the target org still has to pass live classification and the
allowlist. The grant relaxes **who may invoke** an org operation, never **which orgs may be
written**. A capability run against a production org stays impossible with a grant active,
because that decision is made somewhere the grant does not reach.

## The code

### 1. `hooks/lib.py` — the validator, beside `_prod_session_valid`

```python
MAINTAINER_GRANT = ANCHOR / "maintainer.grant"

def maintainer_grant_valid(tree: str = "") -> dict | None:
    """A signed, unexpired, tree-bound window allowing the agent to edit Torque's OWN source.

    Mirrors _prod_session_valid: a window, not a single-use token, revoked by deleting the
    file. It authorizes ARTIFACT edits only — no caller in the org-authorization path consults
    it, and none should. Forging needs the anchor secret, which is operator-only; that is the
    same Layer-0 boundary every other approval rests on.

    Bound to a tree so a window opened to develop Torque cannot silently authorize edits in
    some other workspace that happens to be open.
    """
    try:
        g = json.loads(MAINTAINER_GRANT.read_text())
    except Exception:
        return None
    sig = g.pop("sig", None)
    if not sig or not _hmac.compare_digest(sig, sign(g)):
        return None
    if g.get("exp", 0) <= time.time():
        return None
    if g.get("tree") != (tree or str(TORQUE_HOME.resolve())):
        return None
    return g
```

### 2. `hooks/prod_write_gate.py` — consult it in `handle_edit` only

```python
def handle_edit(tinput):
    paths = {tinput.get("file_path", "")} | set(_tool_paths(tinput))
    for path in sorted(p for p in paths if p):
        if not _protected_reason(path):
            continue
        # The anchor and the auth store are checked FIRST and are never grantable. A window
        # that could rewrite the signing secret could extend itself.
        if shellparse.anchor_ref(path) or _is_sf_auth(path) or shellparse.sf_auth_ref(path):
            lib.deny(f"the trust anchor is never agent-writable, grant or no grant",
                     "artifact-edit", HOOK)
        g = lib.maintainer_grant_valid()
        if g:
            # Durable: a maintainer edit that cannot be recorded is not one this tool allows,
            # for the same reason P1-003 gave for production writes.
            if not lib.audit("MAINTAINER-EDIT", f"{path}", durable=True):
                lib.deny("maintainer edit could not be recorded; refusing rather than "
                         "editing the enforcement layer without an audit trail",
                         "artifact-edit", HOOK)
            continue
        lib.deny(f"agent modification of protected file {os.path.basename(path)} is denied; "
                 f"operator-present issuance only — {lib.approve_cmd('--maintainer', '<minutes>')}",
                 "artifact-edit", HOOK)
    lib.allow()
```

### 3. `bin/torque-approve` — mint it, after the presence check, before org resolution

```python
    if "--maintainer" in args:
        mins = min(int(args[args.index("--maintainer") + 1]), MAINTAINER_MAX_MIN)   # <= 120
        tree = str(lib.TORQUE_HOME.resolve())
        print(f"\nThis opens a {mins}-minute window in which the agent may edit Torque's own")
        print(f"source under {tree} — hooks, gates, checks, skills.")
        print("It does NOT authorize any org write. Type MAINTAIN to confirm.")
        if not _confirm_literal("MAINTAIN"):
            print("refused", file=sys.stderr); sys.exit(3)
        _ensure_secret()
        g = {"tree": tree, "exp": int(time.time()) + mins * 60, "iat": int(time.time())}
        g["sig"] = lib.sign(g)
        lib.ANCHOR.mkdir(parents=True, exist_ok=True); os.chmod(lib.ANCHOR, 0o700)
        lib.MAINTAINER_GRANT.write_text(json.dumps(g)); os.chmod(lib.MAINTAINER_GRANT, 0o600)
        lib.audit("MAINTAINER-GRANT", f"{mins}m window opened for {tree}", durable=True)
        print(f"maintainer window open for {mins} min. Revoke: torque approve --end-maintainer")
        return
```

`--end-maintainer` unlinks the grant and audits the revocation.

## The checks it must ship with

Per the house rule, and each has to be able to fail:

1. **`maintainer_grant_is_operator_only`** — with no grant, a protected edit denies; with a grant
   whose signature is forged, whose `exp` has passed, or whose `tree` names a different
   workspace, it still denies; with a valid grant it allows and writes a `MAINTAINER-EDIT`
   record. Four negative cases and one positive, because the negatives are the security property.
2. **`maintainer_grant_never_touches_orgs`** — with a valid grant active: a production write is
   still denied, a bulk delete still demands its impact-bound token, and an unallowlisted org is
   still refused. This is the check that keeps the split honest as the code moves.
3. **`maintainer_grant_cannot_reach_the_anchor`** — with a valid grant active, Edit/Write against
   `~/.torque/secret`, the token store and the sf auth store are all still denied.
4. **A mutator** — neuter the `exp` comparison so an expired grant is accepted, and require the
   suite to go red. If that cannot be made to fail on purpose, the window is not really bounded.

Note the trap that A1 fell into: `shadow_cannot_escape_the_transaction` passed for eight commits
because its target org refused everything for an unrelated reason, so the assertion never
exercised the guard. Check 1 must assert the *reason* — that an edit was allowed and audited —
not merely that some call returned zero.

## Bootstrapping

The first application cannot be the agent's, by construction. Apply the three edits, then run
the checks. Once the mechanism exists, every later change to it is made under it, audited, in a
bounded window — which is the property that makes this different from switching the gates off.

## The honest caveat

This narrows, but does not close, the disclosed Layer-0 boundary. An operator-present grant means
a human deliberately opened a window; it does not make the agent's edits correct. Everything the
agent writes to `hooks/` during a window still has to survive review and the harness — the grant
buys write access, not trust. Keeping windows short and reading the `MAINTAINER-EDIT` records is
the point of them being time-boxed and audited at all.
