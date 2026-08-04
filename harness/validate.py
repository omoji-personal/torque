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
import pathlib

ROOT = Path(__file__).resolve().parent.parent
CHECKS = ROOT / "harness" / "checks"
DENYLIST = Path.home() / "Desktop" / "torque-planning" / "denylist.txt"   # PRIVATE, external
PROFILES = ("static", "capability", "release")
RANK = {p: i for i, p in enumerate(PROFILES)}

# ---- outcomes -------------------------------------------------------------
# NA = an operator-only check a third party structurally cannot run (it needs a private input
# that is deliberately not published). It is reported honestly and does NOT degrade the verdict
# — otherwise every stranger's first run is red for a reason that has nothing to do with them.
PASS, FAIL, WARN, SKIP, NA = "PASS", "FAIL", "WARN", "SKIP", "N/A"

# An org that has spent its daily API budget refuses everything, including the endpoint that
# would tell you how much is left. Every live check then fails at once, for a reason that has
# nothing to do with any of them. Reporting that as FAIL sends the reader to debug working
# code — so it gets its own outcome, and the run is DEGRADED rather than failed.
LIMITED = "LIMIT"
_LIMIT_MARKERS = ("REQUEST_LIMIT_EXCEEDED", "TotalRequests Limit exceeded")


def rate_limited(detail: str) -> bool:
    return any(m in (detail or "") for m in _LIMIT_MARKERS)

def _operator_mode():
    """True on the author's machine — where the private denylist MUST exist and its absence is a
    hard failure. Detected by the planning directory (a third-party clone never has it), or an
    explicit override. This keeps clean-IP fail-closed for the operator while letting a stranger
    run everything else green."""
    env = os.environ.get("TORQUE_OPERATOR")
    if env == "0":                 # explicit third-party simulation (testing the stranger path)
        return False
    return env == "1" or DENYLIST.parent.exists()

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
        if _operator_mode():                      # author's machine: absence is a HARD failure
            return Result("clean_ip", FAIL, f"FAIL-CLOSED: {err}", third_party=False)
        # third-party clone: the denylist is private by design, so this check cannot run here.
        return Result("clean_ip", NA, "operator-only check (private denylist not published); "
                      "the published tree was scanned clean at release — see harness/VALIDATION.md",
                      third_party=False)
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
    scanned, unreadable = 0, []
    for f in sh("git", "ls-files").stdout.splitlines():
        p = ROOT / f
        if str(p.resolve()) == self_path:      # exact-path self-exemption
            continue
        try:
            body = p.read_text(errors="ignore")
        except Exception as e:
            # A file the scanner could not open is a file the scanner cannot clear. Skipping it
            # silently and then reporting "tracked files clean" states something stronger than
            # was checked, which is the precise shape of a scanner nobody should trust.
            unreadable.append(f"{f} ({type(e).__name__})")
            continue
        scanned += 1
        if rx.search(body):
            return Result("secret_scan", FAIL, f"secret-shaped token in {f}")
    if unreadable:
        return Result("secret_scan", FAIL,
                      f"{len(unreadable)} tracked file(s) could not be read, so cannot be "
                      f"declared clean: {unreadable[:3]}")
    return Result("secret_scan", PASS,
                  f"{len(_SECRET_BITS)} patterns across {scanned} tracked files, all readable, "
                  f"all clean")

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
          "PASS": PASS, "FAIL": FAIL, "WARN": WARN, "SKIP": SKIP, "NA": NA,
          "REGISTRY": REGISTRY,          # so a check can validate labels naming other checks
          "subprocess": subprocess, "json": json, "os": os, "re": re, "Path": Path}
    broken = []
    for p in sorted((ROOT / "harness" / "checks").glob("check_*.py")):
        try:
            exec(compile(p.read_text(), str(p), "exec"), ns)
        except Exception as e:
            print(f"  ! plugin {p.name} failed to load: {e}")
            broken.append(p.name)
    return broken

# A plugin that fails to import used to print a warning and vanish — its checks simply never
# registered, and the run then reported PASS. A syntax error in a check file therefore turned
# that check green, which is the precise failure mode this harness exists to catch. Observed for
# real: an edit broke check_p2_probe.py and the verdict stayed PASS with probe_cycle absent.
# Registered here so the runner can refuse to report a verdict it cannot stand behind.
BROKEN_PLUGINS = _load_check_plugins()

# ---- runner ---------------------------------------------------------------
def org_out_of_budget(target):
    """One cheap probe: has this org spent its daily API allowance?

    Worth doing once, up front, because exhaustion makes every live check fail at the same
    moment for a reason that belongs to none of them. Most checks report only their own
    interpretation of the failure — "known field did not resolve", "dry-run failed" — so
    without this the run reads as a dozen unrelated defects. `sf limits` is itself refused
    once the budget is gone, which is a serviceable detector in its own right.
    """
    if not target:
        return False
    import subprocess as _s
    try:
        r = _s.run(["sf", "limits", "api", "display", "--target-org", target, "--json"],
                   capture_output=True, text=True, timeout=90)
        return rate_limited((r.stdout or "") + (r.stderr or ""))
    except Exception:
        return False


def run_profile(profile, target, only=None):
    want = RANK[profile]
    results = []
    spent = org_out_of_budget(target) if RANK[profile] > RANK["static"] else False
    if spent:
        print("  ⧗ this org has spent its daily API request budget. Checks that need the org "
              "cannot reach a trustworthy conclusion; they are reported ⧗ and this run will "
              "NOT be a pass.")
    for name, lowest, cat, fn in REGISTRY:
        if only and name != only:
            continue
        if not only and RANK[lowest] > want:
            continue
        try:
            res = fn(target) if "target" in fn.__code__.co_varnames else fn()
        except Exception as e:
            res = Result(name, FAIL, f"check raised: {e}")
        # An exhausted org cannot be distinguished from a broken check, so it is reported as
        # neither. This never turns a failure green — the verdict degrades and cannot pass.
        if spent and res.outcome == FAIL and RANK[lowest] > RANK["static"]:
            res = Result(name, FAIL,
                         f"REQUEST_LIMIT_EXCEEDED — the org is out of budget, and this failure "
                         f"could not be distinguished from that. Original: {res.detail}")
        results.append(res)
    return results

def print_report(profile, results, only=None, allow_skip=None):
    if only is not None and not results:
        print(f"\n  ! no check named {only!r}. A filter that matches nothing is not a pass.")
        print("  → verdict: FAIL")
        return "FAIL"
    if BROKEN_PLUGINS:
        print(f"\n  ! {len(BROKEN_PLUGINS)} check plugin(s) failed to load: "
              f"{', '.join(BROKEN_PLUGINS)}")
        print("  → verdict: FAIL (a check that cannot run is never a pass)")
        return "FAIL"
    print(f"\n=== Torque validation — profile: {profile} ===")
    verdict = PASS
    seen = []
    for r in results:
        outcome = LIMITED if (r.outcome == FAIL and rate_limited(r.detail)) else r.outcome
        mark = {PASS:"✓", FAIL:"✗", WARN:"!", SKIP:"−", NA:"·", LIMITED:"⧗"}[outcome]
        tp = "" if r.third_party else " [operator-reproducible]"
        print(f"  {mark} {r.name:22} {outcome:5} {r.detail}{tp}")
        seen.append(outcome)
        if outcome == FAIL: verdict = FAIL
        elif outcome == SKIP and r.name in (allow_skip or {}):
            # An ACKNOWLEDGED skip: the operator named this check and gave a reason, so it is
            # not an unexplained gap. The run is still DEGRADED — never PASS — because the check
            # did not run and no flag can make an unasked question answered. What the flag buys
            # is a zero exit, so a pipeline can proceed on a gap someone has looked at and
            # accepted, instead of on one nobody noticed.
            print(f"      ↳ skip allowed: {allow_skip[r.name]}")
            if verdict == PASS: verdict = "DEGRADED"
        elif outcome in (SKIP, LIMITED, WARN) and verdict == PASS: verdict = "DEGRADED"
        # WARN degrades. It did not, which made every warning decoration — a check could report
        # that something was wrong and the run would still say PASS, which is the shape of
        # problem this project exists to refuse (release panel, codex/gpt-5.6-sol).
        # NA never degrades: it is an operator-only check, reported honestly, not a gap.
    if LIMITED in seen:
        print("  ⧗ the org is out of daily API requests — those checks did not run and did not "
              "fail. Re-run after the rolling window clears.")
    print(f"  → verdict: {verdict}")
    return verdict

# ---- self-test: mutators for catastrophe-class checks ---------------------
_MUTATED_FILES = ("hooks/shellparse.py", "hooks/destructive_data_gate.py", "hooks/lib.py",
                  "hooks/prod_write_gate.py")


def _mutant_residue():
    """Files still carrying a mutation from a previous run. A leftover MUTANT is not cosmetic:
    it is a neutered guard in a hook that is REGISTERED and running, so the gate fails open
    until someone notices. Refuse to start rather than mutate on top of it."""
    out = []
    for rel in _MUTATED_FILES:
        f = ROOT / rel
        try:
            if f.exists() and "# MUTANT" in f.read_text():
                out.append(rel)
        except Exception:
            pass
    return out


def _self_test_guard():
    """Exclusive lock + crash-safe restore for the window in which live hooks are mutated."""
    import atexit, fcntl, signal, shutil, tempfile
    residue = _mutant_residue()
    if residue:
        print("  ! REFUSING to run: a previous self-test left mutations in "
              f"{', '.join(residue)}.\n"
              "    Those are live PreToolUse hooks — restore them before continuing:\n"
              "      git checkout -- " + " ".join(residue), file=sys.stderr)
        raise SystemExit(1)

    lockf = open(ROOT / "local" / ".self-test.lock", "w") if (ROOT / "local").exists() \
        else tempfile.NamedTemporaryFile(prefix="torque-selftest-", delete=False, mode="w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("  ! another self-test is running (it mutates live hooks); refusing to interleave",
              file=sys.stderr)
        raise SystemExit(1)

    backup = tempfile.mkdtemp(prefix="torque-selftest-backup-")
    for rel in _MUTATED_FILES:
        f = ROOT / rel
        if f.exists():
            shutil.copy2(f, pathlib.Path(backup) / rel.replace("/", "_"))

    def _restore(*_a):
        for rel in _MUTATED_FILES:
            b = pathlib.Path(backup) / rel.replace("/", "_")
            try:
                if b.exists() and "# MUTANT" in (ROOT / rel).read_text():
                    shutil.copy2(b, ROOT / rel)
            except Exception:
                pass
    atexit.register(_restore)
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            prev = signal.getsignal(sig)
            signal.signal(sig, lambda s, fr, _p=prev: (_restore(), sys.exit(130)))
        except Exception:
            pass
    return _restore


def self_test(target=None):
    # HARD GUARD: a mutator spawns validate.py as a subprocess; without this the child runs
    # self_test() too and mutates the same source again — recursively. A killed run then
    # leaves the tree broken. Observed for real: 16 stacked MUTANT lines in hooks/lib.py.
    if os.environ.get("TORQUE_IN_SELFTEST") == "1":
        return True
    os.environ["TORQUE_IN_SELFTEST"] = "1"
    _restore_hooks = _self_test_guard()
    print("=== --self-test: proving catastrophe-class checks can FAIL ===")
    ok = True
    TOTAL_MUTATORS = 15            # keep in step with the mutators below; asserted by the count check
    skipped = []                   # (label, count) — mutators that could not run; never read as caught
    op = _operator_mode()          # clean-IP mutators need the private pattern list
    if not op:
        print("  · clean_ip mutators (3): operator-only — the private pattern list is not "
              "published, so they are skipped rather than failed")
        skipped.append(("clean_ip ×3 (operator-only)", 3))
    # clean_ip: a tracked file with a denied term must FAIL. Use the synthetic sentinel
    # pattern (in the private denylist) so the harness source never embeds a real
    # prohibited name — otherwise clean_ip would flag its own mutator.
    victim = ROOT / "harness" / "_mutant.txt"
    sentinel = "TORQUE_CLEANIP_SELFTEST_" + "SENTINEL"   # split so THIS line isn't a hit
    if op:
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
        if not op: raise RuntimeError("operator-only")
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
    except RuntimeError:
        pass
    finally:
        sh("git","update-ref","-d","refs/selftest/hist")
        sh("git","reflog","expire","--expire=now","--all"); sh("git","gc","--prune=now","--quiet")
    # clean_ip fail-closed: absent denylist must FAIL (simulate via rename)
    if op and DENYLIST.exists():
        bak = DENYLIST.with_suffix(".bak")
        try:
            DENYLIST.rename(bak)
            r = _clean_ip()
            passed = r.outcome == FAIL and "FAIL-CLOSED" in r.detail
            # The word "mutator" is load-bearing, not decoration. Two readers parse these lines on
            # that exact token: named_mutators_exist, to derive the set of real mutator names, and
            # torque-attest:61, to record which ones a run caught. This line lacked it, so both
            # read 14 while TOTAL_MUTATORS said 15, and every attestation counted a mutator it
            # could not name. A count and a list disagreeing, inside the artifact offered as
            # evidence, is the exact defect this harness exists to catch.
            print(f"  {'✓' if passed else '✗'} clean_ip fail-closed (denylist absent) mutator: expected FAIL, got {r.outcome}")
            ok &= passed
        finally:
            bak.rename(DENYLIST)
    # anchor-guard mutator: neuter shellparse.anchor_ref; a secret-read must then NO LONGER be
    # denied — proving that guard is load-bearing (audit K-1/R-01). Restored in finally.
    spf = ROOT / "hooks" / "shellparse.py"
    orig = spf.read_text()
    try:
        spf.write_text(orig.replace(
            "def anchor_ref(tok, cwd=None, varmap=None) -> bool:",
            "def anchor_ref(tok, cwd=None, varmap=None) -> bool:\n    return False  # MUTANT", 1))
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
        spf.write_text(orig.replace(
            "def _write_shape_targets(argv):",
            "def _write_shape_targets(argv):\n    return \"\", []  # MUTANT", 1))
        # target a protected-DIR file that is NOT basename-listed, so ONLY redirect detection
        # can catch it — proving the write-shape target extraction is load-bearing (a basename-
        # listed file would be denied regardless of the redirect parse).
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
            "def _abs_pattern(tok, cwd=None, varmap=None):",
            "def _abs_pattern(tok, cwd=None, varmap=None):\n    return os.path.expanduser(tok)  # MUTANT", 1))
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
    # command-word mutator: neuter cmd_base's expansion, so the command word is dispatched as the
    # literal it was written as. `/usr/local/bin/s[f] data delete …` must then NO LONGER be denied
    # — which is precisely what shipped until this round, across every gate and every operation.
    orig5 = spf.read_text()
    try:
        spf.write_text(orig5.replace(
            "def cmd_base(word, vocab=None):",
            "def cmd_base(word, vocab=None):\n"
            "    return os.path.basename(word or '').lower()  # MUTANT", 1))
        ev = json.dumps({"tool_name": "Bash", "tool_input": {"command":
            "/usr/local/bin/s[f] data delete record --sobject Account --record-id 001x "
            "--target-org sf-prod"}})
        r = subprocess.run([sys.executable, str(ROOT / "hooks" / "destructive_data_gate.py")],
                           input=ev, capture_output=True, text=True, cwd=ROOT, timeout=30)
        r2 = subprocess.run([sys.executable, str(ROOT / "hooks" / "prod_write_gate.py")],
                            input=ev, capture_output=True, text=True, cwd=ROOT, timeout=30)
        passed = 2 not in (r.returncode, r2.returncode)
        print(f"  {'✓' if passed else '✗'} command-word (cmd_base) mutator: expected NOT-deny from "
              f"either gate, got exits {r.returncode}/{r2.returncode}")
        ok &= passed
    finally:
        spf.write_text(orig5)
    # deadline mutator: neuter _arm_deadline, so the budget goes back to being advisory. The
    # budget check must then FAIL — proving it tests the deadline's behaviour and not just the
    # arithmetic, which was right the whole time the gate was overrunning by 16 seconds.
    lbf = ROOT / "hooks" / "lib.py"
    orig6 = lbf.read_text()
    try:
        lbf.write_text(orig6.replace(
            "def _arm_deadline(seconds: float, hook_id: str = \"\"):",
            "def _arm_deadline(seconds: float, hook_id: str = \"\"):\n    return  # MUTANT", 1))
        fn = dict((n, f) for n, _p, _c, f in REGISTRY).get("budget_fits_hook_timeout")
        r = fn()
        passed = r.outcome == FAIL
        print(f"  {'✓' if passed else '✗'} deadline (_arm_deadline) mutator: expected FAIL, got {r.outcome}")
        ok &= passed
    finally:
        lbf.write_text(orig6)
    # single-use mutator: replace the atomic rename-claim with the exists-then-read shape a
    # refactor would naturally reach for. Concurrent claimers must then BOTH be authorized —
    # proving the check races the claim rather than merely calling it twice in a row.
    orig7 = lbf.read_text()
    try:
        lbf.write_text(orig7.replace(
            "        os.rename(p, claim)                           # atomic single-use claim",
            "        import shutil; shutil.copyfile(p, claim)  # MUTANT: read without claiming\n"
            "        os.unlink(p) if False else None", 1))
        fn = dict((n, f) for n, _p, _c, f in REGISTRY).get("token_is_single_use")
        r = fn()
        passed = r.outcome == FAIL
        print(f"  {'✓' if passed else '✗'} single-use (rename-claim) mutator: expected FAIL, got {r.outcome}")
        ok &= passed
    finally:
        lbf.write_text(orig7)
    # identity-binding mutator: stop requiring the Organization row to belong to the org that
    # `org display` identified. The check must then FAIL — proving it tests the binding and not
    # merely that classification returns something.
    orig8 = lbf.read_text()
    try:
        lbf.write_text(orig8.replace(
            "    if orgid and norm_id(rec.get(\"Id\")) != orgid:",
            "    if False:  # MUTANT", 1))
        fn = dict((n, f) for n, _p, _c, f in REGISTRY).get("classification_is_identity_bound")
        r = fn()
        passed = r.outcome == FAIL
        print(f"  {'✓' if passed else '✗'} identity-binding mutator: expected FAIL, got {r.outcome}")
        ok &= passed
    finally:
        lbf.write_text(orig8)
    # redaction mutator: monkeypatch lib.redact to identity and call the session-log check.
    # Editing the FILE does nothing here — lib is already imported, so the check would keep using
    # the cached function and report a false PASS. Patching the live module object is both the
    # correct mutation and the safest: no file is touched, so an interrupted run cannot leave a
    # broken tree (an earlier file-editing version recursively fork-bombed and did exactly that).
    if target:
        import importlib
        _lib_mod = importlib.import_module("lib")
        _real_redact = _lib_mod.redact
        try:
            _lib_mod.redact = lambda t: t                      # neutered
            fn = dict((n, f) for n, _p, _c, f in REGISTRY).get("session_log_integrity")
            r = fn(target) if fn else None
            passed = r is not None and r.outcome == FAIL
            print(f"  {'✓' if passed else '✗'} redaction mutator: expected session-log FAIL, got "
                  f"{r.outcome if r else 'no check'}")
            ok &= passed
        finally:
            _lib_mod.redact = _real_redact                     # always restore
    else:
        # Without an org the session-log check cannot run, so neither can its mutator. SAY SO.
        # Printing "ALL MUTATORS CAUGHT" while silently omitting one is exactly the false-green
        # this harness exists to prevent: a skip must never be able to read as a pass.
        print("  · redaction mutator: needs --target-org (the session-log check queries an org) "
              "— skipped rather than counted")
        skipped.append(("redaction (needs --target-org)", 1))
    if ok:
        ran = TOTAL_MUTATORS - sum(c for _, c in skipped)
        verdict = (f"ALL {ran} MUTATORS CAUGHT" if not skipped else
                   f"all runnable mutators caught — NOT RUN: "
                   f"{'; '.join(l for l, _ in skipped)}")
    else:
        verdict = "FAILURE — a check did not fail when it should"
    print(f"\n  self-test: {verdict}")
    return ok

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=PROFILES, default="static")
    ap.add_argument("--target-org")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--only", help="run a single named check (fast, targeted)")
    ap.add_argument("--allow-skip", action="append", metavar="CHECK:REASON",
                    help="acknowledge one check's SKIP with a stated reason (repeatable). The "
                         "run stays DEGRADED — never PASS — but exits 0. Refused for release.")
    a = ap.parse_args()
    # `--allow-skip=<id>:<reason>`, repeatable. Documented in .claude/rules/validation.md since
    # the contract was written, and NOT IMPLEMENTED until now — the same shape as TOOLCHAIN.md's
    # preflight that never existed. A rule file describing a flag the tool does not accept is a
    # contract nobody can comply with, and it went unnoticed because enforcement_map checks that
    # ENFORCEMENT labels resolve to real checks, not that the mechanisms the prose describes are
    # real.
    allow_skip = {}
    for spec in a.allow_skip or []:
        name, _, reason = spec.partition(":")
        if not name or not reason.strip():
            print(f"--allow-skip needs <check-id>:<reason>, got {spec!r}. A skip without a "
                  f"stated reason is the thing this flag exists to prevent.", file=sys.stderr)
            sys.exit(2)
        allow_skip[name.strip()] = reason.strip()
    if allow_skip and a.profile == "release":
        # The contract says release refuses skips entirely: a release verdict is the one claim
        # that must mean every check ran.
        print("--allow-skip is refused in the release profile: a release run may not carry an "
              "unrun check, however well explained.", file=sys.stderr)
        sys.exit(2)
    if a.self_test:
        sys.exit(0 if self_test(a.target_org) else 1)
    # self-test runs as part of static and above
    st_ok = True if a.only else self_test(a.target_org)
    results = run_profile(a.profile, a.target_org, a.only)
    unknown = allow_skip.keys() - {r.name for r in results}
    if unknown:
        print(f"\n  ! --allow-skip names check(s) this profile does not run: {sorted(unknown)}. "
              f"An allowance for a check that never runs hides the day it starts to.")
        print("  → verdict: FAIL")
        sys.exit(1)
    verdict = print_report(a.profile, results, a.only, allow_skip)
    sys.exit(0 if (verdict == PASS and st_ok) else
             0 if (verdict == "DEGRADED" and st_ok and allow_skip and
                   all(r.outcome != FAIL for r in results) and
                   all(r.name in allow_skip for r in results if r.outcome == SKIP)) else 1)

if __name__ == "__main__":
    main()
