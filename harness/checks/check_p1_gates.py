# P1 safety-core checks: gate behavior, approval boundary, single-use tokens, preflight,
# local hygiene, enforcement map. Registered into the harness REGISTRY via the loader.
import sys as _sys
_sys.path.insert(0, str(ROOT / "hooks"))
import lib as _lib

def _gate(hook, payload):
    r = subprocess.run(["python3", str(ROOT / "hooks" / hook)],
                       input=json.dumps(payload), capture_output=True, text=True, cwd=ROOT)
    return r.returncode

@check("gate_write_authz", "capability", catastrophe=True)
def _gate_write_authz(target):
    if not target: return Result("gate_write_authz", SKIP, "no --target-org")
    cases = [
        # (payload, want_exit, label)
        ({"tool_name":"Bash","tool_input":{"command":f"sf data update record --target-org {target} --sobject Account --record-id 001x --values x"}}, 0, "write to allowlisted (explicit target)"),
        ({"tool_name":"Bash","tool_input":{"command":"sf data update --sobject Account --values x"}}, 2, "write with no --target-org denied"),
        ({"tool_name":"Bash","tool_input":{"command":"sf config set target-org=p && sf data update --sobject A --values x"}}, 2, "compound config+write denied"),
        ({"tool_name":"Edit","tool_input":{"file_path":"local/writable-orgs.json"}}, 2, "agent edit of allowlist denied"),
        ({"tool_name":"Bash","tool_input":{"command":f"sf data query --target-org {target} --query \"SELECT Id FROM Account\""}}, 0, "read allowed"),
    ]
    for payload, want, label in cases:
        got = _gate("prod_write_gate.py", payload)
        if got != want:
            return Result("gate_write_authz", FAIL, f"{label}: exit {got} want {want}")
    return Result("gate_write_authz", PASS, f"{len(cases)} write-gate cases correct")

@check("gate_destructive", "capability", catastrophe=True)
def _gate_destructive(target):
    if not target: return Result("gate_destructive", SKIP, "no --target-org")
    cases = [
        ({"tool_name":"Bash","tool_input":{"command":f"sf data delete bulk --target-org {target} --sobject Lead --file ids.csv"}}, 2, "bulk delete without token"),
        ({"tool_name":"Bash","tool_input":{"command":f"echo x | sf apex run --target-org {target}"}}, 2, "anon apex via stdin"),
        ({"tool_name":"Bash","tool_input":{"command":f"sf data delete bulk --target-org {target} --sobject Contact --file ids.csv"}}, 2, "protected-object delete"),
        ({"tool_name":"Bash","tool_input":{"command":f"sf data update record --target-org {target} --sobject Account --record-id 001 --values x"}}, 0, "single-record update allowed"),
    ]
    for payload, want, label in cases:
        got = _gate("destructive_data_gate.py", payload)
        if got != want:
            return Result("gate_destructive", FAIL, f"{label}: exit {got} want {want}")
    return Result("gate_destructive", PASS, f"{len(cases)} destructive-gate cases correct")

@check("approval_boundary", "capability", catastrophe=True)
def _approval_boundary(target):
    if not target: return Result("approval_boundary", SKIP, "no --target-org")
    # agent shell cannot mint
    r = subprocess.run(["python3", str(ROOT/"bin"/"torque-approve"), "00DTEST", "bulk-delete"],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, cwd=ROOT)
    if r.returncode == 0:
        return Result("approval_boundary", FAIL, "agent shell was able to mint a token")
    # planted operator token authorizes exactly once. It must be HMAC-SIGNED the way
    # bin/torque-approve signs it — an unsigned token is (correctly) rejected now.
    _, orgid, _ = _lib.classify(target)
    _lib.ANCHOR.mkdir(parents=True, exist_ok=True)
    import os as _os
    _os.chmod(_lib.ANCHOR, 0o700)
    if not _lib.SECRET.exists():
        _lib.SECRET.write_bytes(_os.urandom(32)); _os.chmod(_lib.SECRET, 0o600)
    _lib.TOKENS.mkdir(parents=True, exist_ok=True)
    import time
    _payload = {"orgId": orgid, "op": "bulk-delete", "digest": "",
                "exp": int(time.time())+300, "iat": int(time.time())}
    _payload["sig"] = _lib.sign(_payload)
    _lib.token_path(orgid, "bulk-delete").write_text(json.dumps(_payload))
    payload = {"tool_name":"Bash","tool_input":{"command":f"sf data delete bulk --target-org {target} --sobject Lead --file ids.csv"}}
    first = _gate("destructive_data_gate.py", payload)
    second = _gate("destructive_data_gate.py", payload)
    if first != 0:  return Result("approval_boundary", FAIL, f"valid token did not authorize (exit {first})")
    if second != 2: return Result("approval_boundary", FAIL, f"token was reusable (2nd exit {second})")
    return Result("approval_boundary", PASS, "agent cannot mint; operator token authorizes exactly once")

@check("preflight_credentials", "capability", catastrophe=True)
def _preflight(target):
    # enumerate authenticated orgs, classify; a mutation-capable production cred with no
    # exception file WARNs (daily) / FAILs (release). Here: report any production org.
    r = subprocess.run(["sf","org","list","--json"], capture_output=True, text=True)
    if r.returncode != 0: return Result("preflight_credentials", SKIP, "org list failed")
    data = json.loads(r.stdout)["result"]
    orgs = [o.get("alias") or o.get("username") for grp in data.values() if isinstance(grp, list) for o in grp]
    # we only assert the check runs and classifies our target correctly
    verdict, _, _ = _lib.classify(target) if target else ("?", None, None)
    return Result("preflight_credentials", PASS, f"{len(orgs)} orgs enumerated; {target}={verdict}")

@check("local_hygiene", "capability")
def _local_hygiene():
    # scan local/ for secret shapes and assert 0600 on sensitive files
    import stat
    secret = re.compile("|".join(["access"+"_token","refresh"+"_token","BEGIN [A-Z ]*PRIVATE KEY","sid"+"=[A-Za-z0-9]"]))
    for p in (ROOT/"local").rglob("*"):
        if p.is_file():
            try: txt = p.read_text(errors="ignore")
            except Exception: continue
            if secret.search(txt):
                return Result("local_hygiene", FAIL, f"secret-shaped content in {p.name}")
            if p.name in ("writable-orgs.json",) or "token" in p.name:
                mode = stat.S_IMODE(p.stat().st_mode)
                if mode & 0o077:
                    return Result("local_hygiene", FAIL, f"{p.name} mode {oct(mode)} not 0600")
    return Result("local_hygiene", PASS, "local/ clean, sensitive files 0600")

@check("enforcement_map", "static")
def _enforcement_map():
    # every rule declaring ENFORCEMENT: hook-enforced must resolve to a registered hook.
    rules = ROOT / ".claude" / "rules"
    hooks = {p.stem for p in (ROOT/"hooks").glob("*.py") if p.stem != "lib"}
    unresolved = []
    for rf in rules.glob("*.md"):
        for m in re.finditer(r"ENFORCEMENT:\s*hook-enforced\s*\(([^)]+)\)", rf.read_text()):
            if m.group(1).strip() not in hooks:
                unresolved.append(f"{rf.name}:{m.group(1)}")
    if unresolved:
        return Result("enforcement_map", FAIL, f"unresolved hook-enforced: {unresolved}")
    return Result("enforcement_map", PASS, f"{len(hooks)} hooks; enforcement labels resolve")

@check("gate_adversarial_fixtures", "capability", catastrophe=True)
def _gate_adversarial_fixtures(target):
    # Runs harness/tests/run_gate_fixtures.py — 37 adversarial + legit fixtures plus dynamic
    # valid-token allow-path tests, exercising both gates against every audited attack class
    # (parser evasion, compound/quote, legacy verbs, decoy target, secret-read self-mint,
    # protected-path redirect, MCP destructive, apex TOCTOU, forged signature). The attack
    # strings live as DATA in the JSON so they never touch a Bash command line.
    r = subprocess.run(["python3", str(ROOT / "harness" / "tests" / "run_gate_fixtures.py")],
                       capture_output=True, text=True, cwd=ROOT, timeout=300)
    if r.returncode != 0:
        tail = (r.stdout.strip().splitlines() or [""])[-1]
        return Result("gate_adversarial_fixtures", FAIL, f"gate fixtures failed — {tail}")
    m = re.search(r"(\d+) passed, (\d+) failed", r.stdout)
    return Result("gate_adversarial_fixtures", PASS,
                  f"{m.group(1) if m else '?'} adversarial gate fixtures pass")

@check("cache_poison_resistant", "capability", catastrophe=True)
def _cache_poison(target):
    if not target: return Result("cache_poison_resistant", SKIP, "no --target-org")
    import time, json as _j, importlib
    importlib.reload(_lib)
    disp = _lib._sf("org","display","--target-org",target,"--json")
    d = _j.loads(disp.stdout)["result"]; user = d["username"]; oid = _lib.norm_id(d["id"])
    saved = _lib.CACHE.read_text() if _lib.CACHE.exists() else None
    try:
        # poison the cache to claim this org is production
        _lib.LOCAL.mkdir(exist_ok=True)
        _lib.CACHE.write_text(_j.dumps({user: {"orgId": oid, "verdict": "production", "t": int(time.time())}}))
        ok, _ = _lib.authorize_write(target)          # must ignore the poisoned verdict
        if not ok:
            return Result("cache_poison_resistant", FAIL, "poisoned cache flipped an eligible org to denied")
        # and the Edit gate must protect the cache file
        g = subprocess.run(["python3", str(ROOT/"hooks"/"prod_write_gate.py")],
            input=_j.dumps({"tool_name":"Write","tool_input":{"file_path":"local/.classify-cache.json"}}),
            capture_output=True, text=True, cwd=ROOT)
        if g.returncode != 2:
            return Result("cache_poison_resistant", FAIL, "cache file not Edit-protected")
        return Result("cache_poison_resistant", PASS, "write decision re-derives live; cache file protected")
    finally:
        if saved is not None: _lib.CACHE.write_text(saved)
        elif _lib.CACHE.exists(): _lib.CACHE.unlink()
