#!/usr/bin/env python3
"""Torque validation harness — the discipline the tool teaches, applied to the tool.

Profiles compose: release ⊇ capability ⊇ static. A check declares its lowest profile;
it runs in that profile and every higher one. SKIP/BLOCKED is never green. --self-test
mutates a fixture, asserts the relevant check FAILs, and restores — proving each
catastrophe-class check can actually fail.

Usage:
  validate.py --profile {static|capability|release} [--target-org ALIAS]
  validate.py --self-test
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKS = ROOT / "harness" / "checks"
DENYLIST = Path.home() / "Desktop" / "torque-planning" / "denylist.txt"   # PRIVATE, external
PROFILES = ("static", "capability", "release")
RANK = {p: i for i, p in enumerate(PROFILES)}

# ---- outcomes -------------------------------------------------------------
PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"

class Result:
    def __init__(self, name, outcome, detail="", third_party=True):
        self.name, self.outcome, self.detail, self.third_party = name, outcome, detail, third_party

# ---- registry: (name, lowest_profile, catastrophe_class, fn) --------------
REGISTRY = []
def check(name, profile="static", catastrophe=False):
    def deco(fn):
        REGISTRY.append((name, profile, catastrophe, fn)); return fn
    return deco

def sh(*args, **kw):
    return subprocess.run(args, capture_output=True, text=True, cwd=ROOT, **kw)

# ---- CHECK: byte budget (auto-loaded rules <= 24KB, CLAUDE.md <= 4KB) ------
@check("byte_budget", "static")
def _byte_budget():
    claude = (ROOT / "CLAUDE.md")
    cbytes = claude.stat().st_size if claude.exists() else 0
    rules = ROOT / ".claude" / "rules"
    rbytes = sum(f.stat().st_size for f in rules.glob("*.md")) if rules.exists() else 0
    if cbytes > 4096:
        return Result("byte_budget", FAIL, f"CLAUDE.md {cbytes}B > 4096")
    if rbytes > 24576:
        return Result("byte_budget", FAIL, f"rules {rbytes}B > 24576")
    return Result("byte_budget", PASS, f"CLAUDE.md {cbytes}B, rules {rbytes}B")

# ---- CHECK: local/ ignored, nothing tracked under it ----------------------
@check("local_ignored", "static")
def _local_ignored():
    r = sh("git", "check-ignore", "local")
    if r.returncode != 0:
        return Result("local_ignored", FAIL, "local/ is NOT gitignored")
    tracked = sh("git", "ls-files", "local/").stdout.strip()
    if tracked:
        return Result("local_ignored", FAIL, f"tracked under local/: {tracked[:80]}")
    return Result("local_ignored", PASS, "local/ ignored, nothing tracked")

# ---- CHECK: tooling-ignore set matches .gitignore exactly -----------------
@check("tooling_ignore_exact", "static")
def _tooling_ignore():
    allowed = {l.split("#")[0].strip() for l in (CHECKS/"tooling-ignore.allowed").read_text().splitlines() if l.split("#")[0].strip()}
    gi = {l.strip() for l in (ROOT/".gitignore").read_text().splitlines() if l.strip() and not l.startswith("#")}
    gi.discard("local/")
    extra = gi - allowed
    if extra:
        return Result("tooling_ignore_exact", FAIL, f".gitignore tooling entries not in allowlist: {extra}")
    return Result("tooling_ignore_exact", PASS, f"{len(allowed)} tooling entries, all declared")

# ---- CHECK: clean-IP — fail-closed denylist over tracked files + history --
def _load_denylist():
    if not DENYLIST.exists():
        return None, "denylist absent"
    pats = [l.strip() for l in DENYLIST.read_text().splitlines() if l.strip() and not l.startswith("#")]
    if len(pats) < 12:
        return None, f"denylist below minimum ({len(pats)} < 12)"
    return pats, None

@check("clean_ip", "static", catastrophe=True)
def _clean_ip():
    pats, err = _load_denylist()
    if err:
        return Result("clean_ip", FAIL, f"FAIL-CLOSED: {err}", third_party=False)
    joined = "|".join(f"({p})" for p in pats)
    rx = re.compile(joined, re.I); rxb = re.compile(joined.encode(), re.I)
    for f_ in sh("git", "ls-files").stdout.splitlines():                    # (1) tracked contents
        p = ROOT / f_
        try:
            if rx.search(p.read_text(errors="ignore")):
                return Result("clean_ip", FAIL, f"denied term in tracked file {f_}", third_party=False)
        except Exception:
            continue
    objs = sh("git", "rev-list", "--objects", "--all").stdout.splitlines()  # (2) history paths
    if rx.search("\n".join(objs)):
        return Result("clean_ip", FAIL, "denied term in a path in history", third_party=False)
    ids = "\n".join(l.split()[0] for l in objs if l.strip()).encode()      # (3) blob contents
    raw = subprocess.run(["git","cat-file","--batch"], input=ids, capture_output=True, cwd=ROOT).stdout
    if rxb.search(raw):
        return Result("clean_ip", FAIL, "denied term in a historical blob's contents", third_party=False)
    if rx.search(sh("git","log","--all","--format=%an%n%cn%n%s%n%b").stdout):  # (4) commit metadata
        return Result("clean_ip", FAIL, "denied term in commit metadata", third_party=False)
    if rx.search(sh("git","for-each-ref","--format=%(contents)","refs/tags").stdout):  # (5) tags
        return Result("clean_ip", FAIL, "denied term in an annotated tag message", third_party=False)
    return Result("clean_ip", PASS, f"{len(pats)} patterns; tree, blobs, paths, metadata, tags clean", third_party=False)

# ---- CHECK: secret_scan — programmatic patterns, self-exempt --------------
_SECRET_BITS = ["00D" + "[A-Za-z0-9]{12,15}", "secur/" + "frontdoor.jsp",
                "sid" + "=", "access" + "_token", "refresh" + "_token",
                "BEGIN [A-Z ]*PRIVATE KEY"]
@check("secret_scan", "static", catastrophe=True)
def _secret_scan():
    rx = re.compile("|".join(_SECRET_BITS))
    self_path = str(Path(__file__).resolve())
    for f in sh("git", "ls-files").stdout.splitlines():
        p = ROOT / f
        if str(p.resolve()) == self_path:      # exact-path self-exemption
            continue
        try:
            if rx.search(p.read_text(errors="ignore")):
                return Result("secret_scan", FAIL, f"secret-shaped token in {f}")
        except Exception:
            continue
    return Result("secret_scan", PASS, f"{len(_SECRET_BITS)} patterns, tracked files clean")

# ---- CHECK: org classification (three-valued; dev != production) ----------
@check("org_classify", "capability", catastrophe=True)
def _org_classify(target):
    if not target:
        return Result("org_classify", SKIP, "no --target-org")
    q = "SELECT Id, IsSandbox, OrganizationType, TrialExpirationDate FROM Organization"
    r = sh("sf", "data", "query", "--target-org", target, "--json", "--query", q)
    if r.returncode != 0:
        return Result("org_classify", FAIL, f"query failed: {r.stderr[:80]}")
    rec = json.loads(r.stdout)["result"]["records"][0]
    is_sandbox = rec.get("IsSandbox")
    otype = rec.get("OrganizationType") or ""
    trial = rec.get("TrialExpirationDate")
    if is_sandbox:
        verdict = "sandbox"
    elif otype == "Developer Edition":
        verdict = "developer"
    elif trial:  # trial-shaped without local scratch evidence
        dh = sh("sf", "org", "list", "--json")
        verdict = "scratch" if target in dh.stdout and '"isScratch": true' in dh.stdout else "production"
    else:
        verdict = "production"
    if verdict != "developer":
        return Result("org_classify", FAIL, f"sf-coffee expected 'developer', got '{verdict}'")
    return Result("org_classify", PASS, f"{target} classified '{verdict}' (IsSandbox={is_sandbox}, {otype})")

# ---- CHECK: live verification helper — real vs hallucinated field ---------
@check("describe_first", "capability", catastrophe=True)
def _describe_first(target):
    if not target:
        return Result("describe_first", SKIP, "no --target-org")
    def field_exists(obj, field):
        r = sh("sf", "data", "query", "--target-org", target, "--use-tooling-api", "--json",
               "--query", f"SELECT QualifiedApiName FROM FieldDefinition WHERE EntityDefinition.QualifiedApiName='{obj}' AND QualifiedApiName='{field}'")
        return r.returncode == 0 and json.loads(r.stdout)["result"]["totalSize"] > 0
    known = field_exists("Account", "Name")
    hallucinated = field_exists("Account", "Torque_Not_A_Real_Field__c")
    if not known:
        return Result("describe_first", FAIL, "known field Account.Name did not resolve")
    if hallucinated:
        return Result("describe_first", FAIL, "hallucinated field wrongly resolved")
    return Result("describe_first", PASS, "known field resolves, hallucinated field refused")

# ---- plugin checks: harness/checks/check_*.py register into the shared REGISTRY -------
def _load_check_plugins():
    ns = {"check": check, "Result": Result, "sh": sh, "ROOT": ROOT, "CHECKS": CHECKS,
          "PASS": PASS, "FAIL": FAIL, "WARN": WARN, "SKIP": SKIP,
          "subprocess": subprocess, "json": json, "os": os, "re": re, "Path": Path}
    for p in sorted((ROOT / "harness" / "checks").glob("check_*.py")):
        try:
            exec(compile(p.read_text(), str(p), "exec"), ns)
        except Exception as e:
            print(f"  ! plugin {p.name} failed to load: {e}")
_load_check_plugins()

# ---- runner ---------------------------------------------------------------
def run_profile(profile, target):
    want = RANK[profile]
    results = []
    for name, lowest, cat, fn in REGISTRY:
        if RANK[lowest] > want:
            continue
        try:
            res = fn(target) if "target" in fn.__code__.co_varnames else fn()
        except Exception as e:
            res = Result(name, FAIL, f"check raised: {e}")
        results.append(res)
    return results

def print_report(profile, results):
    print(f"\n=== Torque validation — profile: {profile} ===")
    verdict = PASS
    for r in results:
        mark = {PASS:"✓", FAIL:"✗", WARN:"!", SKIP:"−"}[r.outcome]
        tp = "" if r.third_party else " [operator-reproducible]"
        print(f"  {mark} {r.name:22} {r.outcome:5} {r.detail}{tp}")
        if r.outcome == FAIL: verdict = FAIL
        elif r.outcome == SKIP and verdict == PASS: verdict = "DEGRADED"
    print(f"  → verdict: {verdict}")
    return verdict

# ---- self-test: mutators for catastrophe-class checks ---------------------
def self_test():
    print("=== --self-test: proving catastrophe-class checks can FAIL ===")
    ok = True
    # clean_ip: a tracked file with a denied term must FAIL. Use the synthetic sentinel
    # pattern (in the private denylist) so the harness source never embeds a real
    # prohibited name — otherwise clean_ip would flag its own mutator.
    victim = ROOT / "harness" / "_mutant.txt"
    sentinel = "TORQUE_CLEANIP_SELFTEST_" + "SENTINEL"   # split so THIS line isn't a hit
    try:
        victim.write_text(f"this mentions {sentinel} inline\n")
        sh("git", "add", "-f", str(victim))
        r = _clean_ip()
        passed = r.outcome == FAIL
        print(f"  {'✓' if passed else '✗'} clean_ip mutator: expected FAIL, got {r.outcome}")
        ok &= passed
    finally:
        sh("git", "rm", "-f", "--cached", str(victim))
        victim.unlink(missing_ok=True)
    # secret_scan: a tracked file with a token shape must FAIL
    victim2 = ROOT / "harness" / "_mutant2.txt"
    try:
        victim2.write_text("access" + "_token=abc123\n")
        sh("git", "add", "-f", str(victim2))
        r = _secret_scan()
        passed = r.outcome == FAIL
        print(f"  {'✓' if passed else '✗'} secret_scan mutator: expected FAIL, got {r.outcome}")
        ok &= passed
    finally:
        sh("git", "rm", "-f", "--cached", str(victim2))
        victim2.unlink(missing_ok=True)
    # clean_ip HISTORICAL BLOB: a denied term reachable only via history must FAIL.
    # Planted as a throwaway ref via plumbing — never touches HEAD, index, or worktree,
    # so it cannot disturb uncommitted work (an earlier --hard version did exactly that).
    sentinel = "TORQUE_CLEANIP_SELFTEST_" + "SENTINEL"
    try:
        blob = subprocess.run(["git","hash-object","-w","--stdin"], input=sentinel.encode(),
                              capture_output=True, cwd=ROOT).stdout.decode().strip()
        tree = subprocess.run(["git","mktree"], input=f"100644 blob {blob}\tf.txt\n".encode(),
                             capture_output=True, cwd=ROOT).stdout.decode().strip()
        commit = subprocess.run(["git","commit-tree",tree,"-m","selftest"], input=b"",
                               capture_output=True, cwd=ROOT,
                               env={**os.environ,"GIT_AUTHOR_NAME":"t","GIT_AUTHOR_EMAIL":"t@t",
                                    "GIT_COMMITTER_NAME":"t","GIT_COMMITTER_EMAIL":"t@t"}).stdout.decode().strip()
        sh("git","update-ref","refs/selftest/hist",commit)
        r = _clean_ip()
        passed = r.outcome == FAIL and "historical blob" in r.detail
        print(f"  {'✓' if passed else '✗'} clean_ip historical-blob mutator: expected FAIL, got {r.outcome}")
        ok &= passed
    finally:
        sh("git","update-ref","-d","refs/selftest/hist")
        sh("git","reflog","expire","--expire=now","--all"); sh("git","gc","--prune=now","--quiet")
    # clean_ip fail-closed: absent denylist must FAIL (simulate via rename)
    if DENYLIST.exists():
        bak = DENYLIST.with_suffix(".bak")
        try:
            DENYLIST.rename(bak)
            r = _clean_ip()
            passed = r.outcome == FAIL and "FAIL-CLOSED" in r.detail
            print(f"  {'✓' if passed else '✗'} clean_ip fail-closed (denylist absent): expected FAIL, got {r.outcome}")
            ok &= passed
        finally:
            bak.rename(DENYLIST)
    # anchor-guard mutator: neuter shellparse.anchor_ref; a secret-read must then NO LONGER be
    # denied — proving that guard is load-bearing (audit K-1/R-01). Restored in finally.
    spf = ROOT / "hooks" / "shellparse.py"
    orig = spf.read_text()
    try:
        spf.write_text(orig.replace(
            "def anchor_ref(tok, cwd=None) -> bool:",
            "def anchor_ref(tok, cwd=None) -> bool:\n    return False  # MUTANT", 1))
        ev = json.dumps({"tool_name": "Bash", "tool_input": {"command": "cat ~/.torque/secret"}})
        r = subprocess.run([sys.executable, str(ROOT / "hooks" / "prod_write_gate.py")],
                           input=ev, capture_output=True, text=True, cwd=ROOT, timeout=30)
        passed = r.returncode != 2                     # guard removed ⇒ no longer a deny
        print(f"  {'✓' if passed else '✗'} anchor-guard mutator: expected NOT-deny, got exit {r.returncode}")
        ok &= passed
    finally:
        spf.write_text(orig)
    # destructive token mutator: neuter _need_token; a bulk delete must then be ALLOWED —
    # proving the token requirement is load-bearing (audit K-8/T10-02). Restored in finally.
    dg = ROOT / "hooks" / "destructive_data_gate.py"
    orig_d = dg.read_text()
    try:
        dg.write_text(orig_d.replace(
            'def _need_token(orgid, op, digest=""):',
            'def _need_token(orgid, op, digest=""):\n    lib.allow()  # MUTANT', 1))
        ev = json.dumps({"tool_name": "Bash", "tool_input": {"command":
            "sf data delete bulk --sobject Log__c --file ids.csv --target-org sf-coffee"}})
        r = subprocess.run([sys.executable, str(dg)], input=ev, capture_output=True,
                           text=True, cwd=ROOT, timeout=90)
        passed = r.returncode == 0                      # token requirement removed ⇒ allowed
        print(f"  {'✓' if passed else '✗'} destructive token mutator: expected ALLOW, got exit {r.returncode}")
        ok &= passed
    finally:
        dg.write_text(orig_d)
    # redirect-detection mutator: neuter the fused-redirect regex; a `2>gate` write must then
    # NO LONGER be denied — proving the write-shape guard catches gate-truncation (audit R11-02).
    spf = ROOT / "hooks" / "shellparse.py"
    orig = spf.read_text()
    try:
        spf.write_text(re.sub(r'REDIR_FUSED = re\.compile\(r".*"\)',
                              'REDIR_FUSED = re.compile(r"NEVERMATCHES")', orig, count=1))
        # target a protected-DIR file that is NOT basename-listed, so ONLY redirect detection
        # can catch it — proving REDIR_FUSED is load-bearing (a basename-listed file would be
        # denied regardless of the redirect parse).
        ev = json.dumps({"tool_name": "Bash", "tool_input": {"command":
            "echo x > harness/checks/check_p2_probe.py"}})
        r = subprocess.run([sys.executable, str(ROOT / "hooks" / "prod_write_gate.py")],
                           input=ev, capture_output=True, text=True, cwd=ROOT, timeout=30)
        passed = r.returncode != 2
        print(f"  {'✓' if passed else '✗'} redirect-detection mutator: expected NOT-deny, got exit {r.returncode}")
        ok &= passed
    finally:
        spf.write_text(orig)
    # wrapper mutator: neuter wrapped_sf; `nice sf … -o prod` must then be allowed — proving the
    # runner guard is what denies relocation-under-a-wrapper (audit R11-04/R11-06).
    orig2 = spf.read_text()
    try:
        spf.write_text(orig2.replace("def wrapped_sf(argv):",
                                     "def wrapped_sf(argv):\n    return False  # MUTANT", 1))
        ev = json.dumps({"tool_name": "Bash", "tool_input": {"command":
            "nice -n 5 sf data create record --sobject Account --values Name=x --target-org acme-prod"}})
        r = subprocess.run([sys.executable, str(ROOT / "hooks" / "prod_write_gate.py")],
                           input=ev, capture_output=True, text=True, cwd=ROOT, timeout=30)
        passed = r.returncode != 2
        print(f"  {'✓' if passed else '✗'} wrapper (wrapped_sf) mutator: expected NOT-deny, got exit {r.returncode}")
        ok &= passed
    finally:
        spf.write_text(orig2)
    # expansion-awareness mutator: neuter _abs_pattern's var/glob wildcarding; a var-constructed
    # secret read must then NO LONGER be denied — proving the guard reasons about post-expansion
    # paths, not literal text (audit T12-01). Restored in finally.
    orig3 = spf.read_text()
    try:
        spf.write_text(orig3.replace(
            "def _abs_pattern(tok, cwd=None):",
            "def _abs_pattern(tok, cwd=None):\n    return os.path.expanduser(tok)  # MUTANT", 1))
        ev = json.dumps({"tool_name": "Bash", "tool_input": {"command":
            "a=.tor; b=que; cat ~/$a$b/secret"}})
        r = subprocess.run([sys.executable, str(ROOT / "hooks" / "prod_write_gate.py")],
                           input=ev, capture_output=True, text=True, cwd=ROOT, timeout=30)
        passed = r.returncode != 2
        print(f"  {'✓' if passed else '✗'} expansion-awareness mutator: expected NOT-deny, got exit {r.returncode}")
        ok &= passed
    finally:
        spf.write_text(orig3)
    # glob-matcher mutator: neuter the DP _glob_reaches; a **/char-class secret read must then NO
    # LONGER be denied — proving the recursive-glob matcher is load-bearing (audit round 13).
    orig4 = spf.read_text()
    try:
        spf.write_text(orig4.replace(
            "def _glob_reaches(pat_parts, tgt_parts):",
            "def _glob_reaches(pat_parts, tgt_parts):\n    return False  # MUTANT", 1))
        ev = json.dumps({"tool_name": "Bash", "tool_input": {"command":
            "cat /Users/**/omidmojtahedi/.[t]orque/sec[r]et"}})
        r = subprocess.run([sys.executable, str(ROOT / "hooks" / "prod_write_gate.py")],
                           input=ev, capture_output=True, text=True, cwd=ROOT, timeout=30)
        passed = r.returncode != 2
        print(f"  {'✓' if passed else '✗'} glob-matcher (_glob_reaches) mutator: expected NOT-deny, got exit {r.returncode}")
        ok &= passed
    finally:
        spf.write_text(orig4)
    print(f"\n  self-test: {'ALL MUTATORS CAUGHT' if ok else 'FAILURE — a check did not fail when it should'}")
    return ok

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=PROFILES, default="static")
    ap.add_argument("--target-org")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        sys.exit(0 if self_test() else 1)
    # self-test runs as part of static and above
    st_ok = self_test()
    results = run_profile(a.profile, a.target_org)
    verdict = print_report(a.profile, results)
    sys.exit(0 if (verdict == PASS and st_ok) else 1)

if __name__ == "__main__":
    main()
