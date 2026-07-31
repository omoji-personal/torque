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
sys.path.insert(0, str(HOOKS))
import lib

GREEN, RED, DIM, RST = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def run_gate(gate, event):
    p = subprocess.run([sys.executable, str(HOOKS / f"{gate}.py")],
                       input=json.dumps(event), capture_output=True, text=True,
                       cwd=str(ROOT), timeout=90)
    return p.returncode, (p.stderr or "").strip()


def main():
    fixtures = []
    for fn in ("gate_fixtures.json", "gate_fixtures_r11.json"):
        p = ROOT / "harness/tests" / fn
        if p.exists():
            fixtures += json.loads(p.read_text()).get("fixtures", [])
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
    verdict, orgid, _ = lib.classify_live("sf-coffee")
    if not orgid:
        print(f"  {RED}skip valid-token tests — sf-coffee not reachable{RST}")
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
              f"sf apex run --file {copy} --target-org sf-coffee"}}
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
               "sf data delete bulk --sobject Log__c --file ids.csv --target-org sf-coffee"}}
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
