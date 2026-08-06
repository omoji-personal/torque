#!/usr/bin/env python3
"""Run the gate fixtures (harness/tests/gate_fixtures.json) through the real hooks as
subprocesses and assert exit codes (2=deny/blocked, 0=allow). Plus dynamic valid-token
allow-path tests that mint an HMAC-signed token the way bin/torque-approve does — proving
the token IS accepted and consumed, so the deny-path isn't just denying everything.

Attack strings live in the JSON as data; the only Bash command this produces is
`python3 hooks/<gate>.py`, so the operator's own sf-write guard hook never scans them.
"""
import json, os, subprocess, sys, time, hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent          # torque/
HOOKS = ROOT / "hooks"

# HERMETIC ANCHOR — set before `import lib`, because lib resolves ANCHOR at import time.
#
# The suite already went to some trouble to be hermetic about `sf`. It was not hermetic about
# the trust anchor, and that is a bigger dependency: the anchor holds the maintainer window, and
# a window grants exactly what the "Edit protected hook" fixture asserts is denied. So running
# the suite on an operator's own machine during a maintainer window reported a correct gate as
# red — observed, not hypothesised. The same exposure applies to any token left in the real
# anchor by an earlier approve.
#
# A throwaway anchor removes the whole class. The token tests below mint into it themselves, so
# nothing is lost by starting empty — that is what they were written to do. The operator's real
# anchor is never read and never written by this suite.
#
# TORQUE_ANCHOR is honoured only when it points somewhere the agent cannot reach anyway; here it
# is a per-run temp directory, so this cannot become a bypass of anything. It is also set for the
# gate subprocesses further down, via the same environment.
import tempfile                                                  # noqa: E402
_HERMETIC_ANCHOR = Path(tempfile.mkdtemp(prefix="torque-fixture-anchor-"))
os.environ["TORQUE_ANCHOR"] = str(_HERMETIC_ANCHOR)

sys.path.insert(0, str(HOOKS))
import lib  # noqa: E402

if lib.ANCHOR != _HERMETIC_ANCHOR:
    # If lib stops honouring TORQUE_ANCHOR, this suite would silently go back to reading the
    # operator's real anchor and the hermeticity above would be a comment describing nothing.
    print(f"refusing: TORQUE_ANCHOR was not honoured (lib.ANCHOR={lib.ANCHOR}); the fixture "
          f"suite would run against the real trust anchor", file=sys.stderr)
    sys.exit(2)

GREEN, RED, DIM, RST = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


_ENV = None
_STUB_ORGID = None

def _home_rel(h):
    """The repo path AS WRITTEN AFTER `cd` (i.e. relative to HOME) — the TQ-006 fixture does
    `cd; printf ... > <path>`. Using the absolute path with the slash stripped resolves to a
    nonexistent ~/Users/... and the gate correctly allows it, turning a good gate red. Falls
    back to the absolute path when the repo lives outside HOME."""
    import os as _o
    home = str(Path.home())
    return _o.path.relpath(h, home) if h.startswith(home + _o.sep) else h


def _stub_env(org):
    """Put a stub `sf` on PATH for the gate subprocesses so the fixture suite is HERMETIC.

    Without this, every fixture triggers real `sf` callouts — the whole corpus x 2 gates x 2
    calls, which takes seconds on the author's warm machine and MINUTES on anyone else's, so the
    very command the README tells strangers to run appeared to hang. The gates' own security code
    is untouched (no test-only env switch inside lib.py, which would itself be a bypass); we only
    control what `sf` means for the child processes.

    The corpus size is deliberately NOT written here. It said "~125" while the tree carried 193
    recorded fixtures, because a number in a comment has nothing that re-derives it — which is
    the defect claimed_counts exists to catch in prose and cannot reach inside a docstring. The
    count is worth knowing and is available where it is checked; the point of this comment is the
    cost, which does not need it.

    The stub reports the FIRST eligible org from the real allowlist, so authorization still has
    to match a genuine allowlisted orgId — the fixtures test the gate logic, while live-org
    behaviour is covered separately by org_classify / probe_cycle / cache_poison_resistant.
    """
    global _ENV, _STUB_ORGID
    if _ENV is not None:
        return _ENV
    orgid = username = None
    try:
        for e in json.loads(lib.ALLOWLIST.read_text()).get("orgs", []):
            if e.get("verdict") in lib.ELIGIBLE:
                orgid, username = e["orgId"], e.get("username", "stub@example.com"); break
        _STUB_ORGID = orgid
    except Exception:
        pass
    if not orgid:
        _ENV = dict(os.environ); return _ENV          # no allowlist: fall back to real sf
    d = Path(__file__).resolve().parent / ".stub-bin"
    d.mkdir(exist_ok=True)
    (d / "sf").write_text(f"""#!/usr/bin/env python3
import json, sys
a = sys.argv[1:]
def out(o): print(json.dumps(o)); sys.exit(0)
tgt = None
for i, x in enumerate(a):
    if x in ("--target-org", "-o", "-u", "--targetusername") and i + 1 < len(a): tgt = a[i+1]
    elif x.startswith("--target-org="): tgt = x.split("=", 1)[1]
if a[:2] == ["org", "display"]:
    if tgt != {org!r}: sys.exit(1)
    out({{"result": {{"id": {orgid!r}, "username": {username!r}, "instanceUrl": "https://example.my.salesforce.com"}}}})
if a[:2] == ["data", "query"]:
    if tgt != {org!r}: sys.exit(1)
    # Id included because classify now requires the Organization row to prove it belongs to
    # the org `org display` identified — an alias is mutable between the two callouts. A
    # double that omits it is not a faithful double: the real CLI returns what you SELECT.
    out({{"result": {{"records": [{{"Id": {orgid!r}, "IsSandbox": False, "OrganizationType": "Developer Edition", "TrialExpirationDate": None}}]}}}})
if a[:2] == ["org", "list"]: out({{"result": {{"scratchOrgs": []}}}})
sys.exit(1)
""")
    (d / "sf").chmod(0o755)
    _ENV = {**os.environ, "PATH": f"{d}:{os.environ.get('PATH','')}"}
    return _ENV


def run_gate(gate, event):
    p = subprocess.run([sys.executable, str(HOOKS / f"{gate}.py")],
                       input=json.dumps(event), capture_output=True, text=True,
                       cwd=str(ROOT), timeout=90, env=_ENV or os.environ.copy())
    return p.returncode, (p.stderr or "").strip()


def main():
    # Fixtures name a WRITABLE org (allowlisted, non-production) and unreachable ones that must
    # classify production. The writable alias is substituted at load time so a third party's own
    # Developer Edition org fills that slot — otherwise the suite only passes on the author's
    # machine. Deny cases need no substitution: any unauthenticated alias classifies production.
    org = os.environ.get("TORQUE_TEST_ORG", "sf-coffee")
    _stub_env(org)   # hermetic: stub sf on PATH for the gate subprocesses
    fixtures = []
    # Discovered, not enumerated. This was a hardcoded tuple of nine filenames, which means a
    # fixture file added and never listed contributes nothing and the suite still reports green —
    # a corpus that silently shrinks is the same defect class as an attestation that silently
    # drops check outcomes. Sorted so the run order stays deterministic.
    discovered = sorted((ROOT / "harness/tests").glob("gate_fixtures*.json"))
    if not discovered:
        print("refusing: no gate_fixtures*.json found — an empty corpus passes every assertion",
              file=sys.stderr)
        return 2
    per_file = []
    for p in discovered:
        raw = p.read_text().replace("sf-coffee", org)
        # Attack paths are written against the DEFAULT anchor (~/.torque). If the operator
        # relocated it with TORQUE_ANCHOR, those strings no longer reach the real anchor and
        # the gate correctly allows them — turning a correct gate into a red suite. Rewrite
        # them to the configured anchor so the fixtures test the anchor that actually exists.
        A = str(lib.ANCHOR)
        H = str(lib.TORQUE_HOME)
        import os as _o2
        for frm, to in (("d=.torque", f"d={_o2.path.basename(A)}"),   # var holds the anchor name
                        ("$HOME/$d/", f"{_o2.path.dirname(A)}/$d/"),  # var-composed, single var
                        ("$HOME/$d$e", A),        # var-composed
                        ("~/$a$b",      A),        # var-composed, tilde form
                        ("~/.torq*",    A[:-3] + "*"),   # glob
                        ("~/.[t]orque", A),        # character class
                        ("~/.torque",   A),        # literal
                        ("Desktop/torque/hooks/", _home_rel(H) + "/hooks/")):
            raw = raw.replace(frm, to)
        found = json.loads(raw).get("fixtures", [])
        per_file.append((p.name, len(found)))
        fixtures += found
    # Print what was loaded. Discovery without a manifest of what it found is how a file that
    # parses to zero fixtures becomes invisible — it contributes nothing and says nothing.
    print(f"  {DIM}corpus: " + ", ".join(f"{n}={c}" for n, c in per_file) +
          f"  → {len(fixtures)} fixtures{RST}\n")

    passed = failed = 0
    fails = []
    for fx in fixtures:
        code, err = run_gate(fx["gate"], fx["event"])
        ok = (code == 2) if fx["expect"] == "deny" else (code == 0)
        tag = f"{GREEN}PASS{RST}" if ok else f"{RED}FAIL{RST}"
        reason = err.split("DENY")[-1][:70] if "DENY" in err else err[:70]
        print(f"  [{tag}] {fx['gate']:22} {fx['id']:38} exit={code} {DIM}{reason}{RST}")
        if ok:
            passed += 1
        else:
            failed += 1
            fails.append((fx["id"], fx["expect"], code, err[:200]))

    # ---- dynamic valid-token allow-path (simulates bin/torque-approve as operator) --------
    print(f"\n  {DIM}— valid-token allow-path (operator-minted HMAC tokens) —{RST}")
    lib.ANCHOR.mkdir(parents=True, exist_ok=True); os.chmod(lib.ANCHOR, 0o700)
    if not lib.SECRET.exists():
        lib.SECRET.write_bytes(os.urandom(32)); os.chmod(lib.SECRET, 0o600)
    lib.TOKENS.mkdir(parents=True, exist_ok=True)
    lib.APPROVED.mkdir(parents=True, exist_ok=True)
    # Mint for the identity the STUB reports, not one resolved live.
    #
    # This used to call classify_live(org), which returns the real orgId — while the stub `sf`
    # on PATH answers with the first eligible org in the allowlist. The two agreed only because
    # the allowlist had exactly one entry; adding a second org meant the token was written at
    # one path and looked for at another, and both valid-token tests failed with a message
    # about operator presence that had nothing to do with the cause. It also made a suite
    # documented as hermetic reach the network.
    orgid = _STUB_ORGID
    if not orgid:
        print(f"  {RED}skip valid-token tests — no eligible org in the allowlist{RST}")
    else:
        def mint(op, digest=""):
            payload = {"orgId": orgid, "op": op, "digest": digest,
                       "exp": int(time.time()) + 300, "iat": int(time.time())}
            payload["sig"] = lib.sign(payload)
            p = lib.token_path(orgid, op, digest)
            p.write_text(json.dumps(payload)); os.chmod(p, 0o600)
            return p

        # (1) apex from the approved immutable copy WITH a matching token
        body = b"System.debug('torque approved test');"
        digest = hashlib.sha256(body).hexdigest()[:16]
        copy = lib.APPROVED / f"{digest}.apex"; copy.write_bytes(body)
        tok = mint("apex", digest)
        ev = {"tool_name": "Bash", "tool_input": {"command":
              f"sf apex run --file {copy} --target-org {org}"}}
        code, err = run_gate("destructive_data_gate", ev)
        ok = code == 0 and not tok.exists()
        print(f"  [{GREEN+'PASS'+RST if ok else RED+'FAIL'+RST}] destructive_data_gate  "
              f"apex w/ valid token + consumed        exit={code} consumed={not tok.exists()}")
        (passed, failed) = (passed + 1, failed) if ok else (passed, failed + 1)
        if not ok:
            fails.append(("apex valid token", "allow+consume", code, err[:200]))

        # (2) bulk-delete on a non-protected object WITH a token
        tok2 = mint("bulk-delete")
        ev2 = {"tool_name": "Bash", "tool_input": {"command":
               f"sf data delete bulk --sobject Log__c --file ids.csv --target-org {org}"}}
        code2, err2 = run_gate("destructive_data_gate", ev2)
        ok2 = code2 == 0 and not tok2.exists()
        print(f"  [{GREEN+'PASS'+RST if ok2 else RED+'FAIL'+RST}] destructive_data_gate  "
              f"bulk-delete w/ valid token + consumed exit={code2} consumed={not tok2.exists()}")
        (passed, failed) = (passed + 1, failed) if ok2 else (passed, failed + 1)
        if not ok2:
            fails.append(("bulk valid token", "allow+consume", code2, err2[:200]))

        # (3) a token with a TAMPERED signature must be rejected (forgery guard)
        p3 = lib.token_path(orgid, "bulk-delete")
        p3.write_text(json.dumps({"orgId": orgid, "op": "bulk-delete", "digest": "",
                                  "exp": int(time.time()) + 300, "iat": int(time.time()),
                                  "sig": "deadbeef" * 8}))
        code3, _ = run_gate("destructive_data_gate", ev2)
        ok3 = code3 == 2
        print(f"  [{GREEN+'PASS'+RST if ok3 else RED+'FAIL'+RST}] destructive_data_gate  "
              f"forged-signature token rejected       exit={code3}")
        (passed, failed) = (passed + 1, failed) if ok3 else (passed, failed + 1)
        try: p3.unlink()
        except OSError: pass

    print(f"\n  {passed} passed, {failed} failed, {passed + failed} total")
    if fails:
        print(f"\n  {RED}Failures:{RST}")
        for fid, exp, code, err in fails:
            print(f"    - {fid}: expected {exp}, got exit {code}\n      {DIM}{err}{RST}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
