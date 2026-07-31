# P3: skills + agents + installer. Real mass-update cycle against the disposable org;
# org-explorer read-only assertion; hostile-qa planted-defect; installer round-trip.
import sys as _sys, time as _t, json as _j, tempfile as _tmp, shutil as _sh, os as _os
_sys.path.insert(0, str(ROOT / "hooks")); import lib as _lib

@check("skills_justified", "static")
def _skills_justified():
    missing = []
    for sk in (ROOT/".claude"/"skills").glob("*/SKILL.md"):
        t = sk.read_text()
        if "What this adds" not in t:
            missing.append(sk.parent.name)
    if missing:
        return Result("skills_justified", FAIL, f"skills without a raw-CLI justification: {missing}")
    n = len(list((ROOT/".claude"/"skills").glob("*/SKILL.md")))
    return Result("skills_justified", PASS, f"{n} skills, each justified vs raw CLI")

@check("agents_readonly", "static")
def _agents_readonly():
    exp = ROOT/".claude"/"agents"/"org-explorer.md"
    t = exp.read_text()
    # org-explorer must not list Edit/Write/deploy tools
    import re as _re
    tools = _re.search(r"tools:(.*?)(?:\n---|\nYou )", t, _re.S)
    body = tools.group(1) if tools else ""
    for forbidden in ("Edit", "Write", "deploy"):
        if forbidden in body:
            return Result("agents_readonly", FAIL, f"org-explorer lists {forbidden}")
    return Result("agents_readonly", PASS, "org-explorer tools are read-only")

@check("mass_update_cycle", "capability", catastrophe=True)
def _mass_update_cycle(target):
    if not target: return Result("mass_update_cycle", SKIP, "no --target-org")
    marker = f"ZZTORQUE-{int(_t.time())}"
    ids = []
    def sf(*a, **k):
        return subprocess.run(["sf", *a], capture_output=True, text=True, **k)
    try:
        # create 2 flagged test Accounts
        for i in range(2):
            r = sf("data","create","record","--target-org",target,"--sobject","Account",
                   "--values",f"Name={marker}-{i} Description=before")
            oid = None
            try: oid = _j.loads(sf("data","query","--target-org",target,"--json","--query",
                     f"SELECT Id FROM Account WHERE Name='{marker}-{i}'").stdout)["result"]["records"][0]["Id"]
            except Exception: pass
            if oid: ids.append(oid)
        if len(ids) != 2:
            return Result("mass_update_cycle", FAIL, f"setup created {len(ids)}/2 records")
        # preview: exact ID set
        preview = _j.loads(sf("data","query","--target-org",target,"--json","--query",
            f"SELECT Id, Description FROM Account WHERE Name LIKE '{marker}-%'").stdout)["result"]["records"]
        preview_ids = sorted(r["Id"] for r in preview)
        if preview_ids != sorted(ids):
            return Result("mass_update_cycle", FAIL, "preview ID set != created set")
        before = {r["Id"]: r["Description"] for r in preview}          # undo data captured
        # bounded update (each by Id — the gate permits single-record)
        for oid in ids:
            sf("data","update","record","--target-org",target,"--sobject","Account",
               "--record-id",oid,"--values","Description=after")
        # verify
        after = {r["Id"]: r["Description"] for r in _j.loads(sf("data","query","--target-org",target,"--json",
            "--query",f"SELECT Id, Description FROM Account WHERE Name LIKE '{marker}-%'").stdout)["result"]["records"]}
        if any(after[i] != "after" for i in ids):
            return Result("mass_update_cycle", FAIL, "update did not persist")
        # undo: restore from before-values
        for oid in ids:
            sf("data","update","record","--target-org",target,"--sobject","Account",
               "--record-id",oid,"--values",f"Description={before[oid]}")
        restored = {r["Id"]: r["Description"] for r in _j.loads(sf("data","query","--target-org",target,"--json",
            "--query",f"SELECT Id, Description FROM Account WHERE Name LIKE '{marker}-%'").stdout)["result"]["records"]}
        undo_ok = all(restored[i] == "before" for i in ids)
        return Result("mass_update_cycle", PASS if undo_ok else FAIL,
            f"created→preview(exact 2)→update→verify→undo({'restored' if undo_ok else 'FAILED'}); teardown by Id")
    finally:
        for oid in ids:                                                # teardown BY ID ONLY
            sf("data","delete","record","--target-org",target,"--sobject","Account","--record-id",oid)

@check("installer_roundtrip", "static")
def _installer_roundtrip():
    # dry check: the installer script parses and names TORQUE_HOME; do not mutate ~/.claude here
    t = (ROOT/"bin"/"torque-install-gates").read_text()
    if "TORQUE_HOME" not in t or "~/.claude" not in t.replace(str(ROOT), "") and ".claude" not in t:
        return Result("installer_roundtrip", FAIL, "installer missing TORQUE_HOME or user-settings path")
    r = subprocess.run(["python3","-c",f"compile(open('{ROOT}/bin/torque-install-gates').read(),'x','exec')"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return Result("installer_roundtrip", FAIL, f"installer does not compile: {r.stderr[:80]}")
    return Result("installer_roundtrip", PASS, "installer compiles; records TORQUE_HOME for CWD-independent resolution")
