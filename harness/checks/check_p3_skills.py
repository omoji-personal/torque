import pathlib
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
    ids, why = [], []
    def sf(*a, **k):
        return subprocess.run(["sf", *a], capture_output=True, text=True, **k)
    try:
        # create 2 flagged test Accounts
        for i in range(2):
            r = sf("data","create","record","--target-org",target,"--sobject","Account",
                   "--values",f"Name={marker}-{i} Description=before")
            oid = None
            q = sf("data","query","--target-org",target,"--json","--query",
                   f"SELECT Id FROM Account WHERE Name='{marker}-{i}'")
            try: oid = _j.loads(q.stdout)["result"]["records"][0]["Id"]
            except Exception:
                # Record why. A harness that fails without saying why costs more than one that
                # never ran: the next person re-runs it, it passes, and they learn nothing.
                why.append((r.returncode, (r.stderr or r.stdout or "").strip()[:180],
                            q.returncode, (q.stderr or "").strip()[:180]))
            if oid: ids.append(oid)
        if len(ids) != 2:
            detail = "; ".join(f"create rc={a} {b!r} / query rc={c} {d!r}" for a, b, c, d in why)
            return Result("mass_update_cycle", FAIL,
                          f"setup created {len(ids)}/2 records — {detail or 'no error captured'}")
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
    # The installer must register EVERY matcher the project registers. It shipped covering only
    # Bash and Edit|Write|MultiEdit, so an operator who ran it — as the guide instructs, to make
    # the gates bind outside this repo — silently lost MCP-write gating and Read gating on the
    # trust anchor everywhere else, while believing they were covered.
    # Grepping the installer's SOURCE for matcher names proved too weak twice over: it cannot
    # tell PreToolUse from PostToolUse (both spell the matcher "Bash"), and it cannot tell which
    # hook script a matcher actually runs. So run the installer for real against a throwaway
    # HOME and compare what it PRODUCED against what the project registers.
    import json as _json, os as _os, re as _re, tempfile as _tf
    proj = _json.loads((ROOT / ".claude" / "settings.json").read_text())

    def _triples(cfg):
        out = set()
        for event, blocks in (cfg.get("hooks") or {}).items():
            for b in blocks or []:
                for h in b.get("hooks") or []:
                    m = _re.search(r"([A-Za-z_][A-Za-z0-9_]*)\.py", h.get("command", ""))
                    out.add((event, b.get("matcher"), m.group(1) if m else "?"))
        return out

    want = _triples(proj)
    with _tf.TemporaryDirectory() as td:
        env = dict(_os.environ, HOME=td)
        r2 = subprocess.run(["python3", str(ROOT / "bin" / "torque-install-gates")],
                            capture_output=True, text=True, env=env, timeout=60)
        produced = pathlib.Path(td) / ".claude" / "settings.json"
        if r2.returncode != 0 or not produced.exists():
            return Result("installer_roundtrip", FAIL,
                          f"installer did not produce settings (rc={r2.returncode}) "
                          f"{(r2.stderr or '')[:120]}")
        have_t = _triples(_json.loads(produced.read_text()))
        # and it must undo itself completely
        subprocess.run(["python3", str(ROOT / "bin" / "torque-install-gates"), "--remove"],
                       capture_output=True, text=True, env=env, timeout=60)
        left = _triples(_json.loads(produced.read_text()))
    if left:
        return Result("installer_roundtrip", FAIL, f"--remove left {sorted(left)} behind")
    missing = want - have_t
    have = {m for _, m, _ in have_t}
    if missing:
        return Result("installer_roundtrip", FAIL,
                      f"installer produces {sorted(have_t)} but the project registers "
                      f"{sorted(want)} — missing {sorted(missing)}; operators would get "
                      f"partial protection that looks complete")
    if "--remove" not in t:
        return Result("installer_roundtrip", FAIL,
                      "installer has no --remove path; the gate it installs denies edits to "
                      "settings.json, so an operator could not uninstall it")
    return Result("installer_roundtrip", PASS,
                  f"installer compiles; {len(have_t)} hook registrations mirror the project exactly "
                  f"(installed and removed against a throwaway HOME); "
                  f"records TORQUE_HOME for CWD-independent resolution")
