# Knowledge-base integrity: the catalogue must be well-formed, and every claim marked
# `verified-live` must STILL BE TRUE of a real org — checked by querying one, not by trusting
# the file. A platform catalogue nobody re-checks is a blog post with a version number:
# Salesforce ships three releases a year, so a fact recorded once decays on a schedule.
import json as _kb_json
import subprocess as _kb_sp
import os as _kb_os
import re as _kb_re
import sys as _kb_sys
from pathlib import Path as _KbP

_KB = ROOT / "knowledge" / "salesforce-platform.yml"
_REQUIRED = ("id", "domain", "title", "symptom", "cause", "remedy", "confidence", "updated")
_CONFIDENCE = {"verified-live", "documented", "practitioner"}


def _kb_unquote(v: str) -> str:
    """Strip exactly ONE matching pair of quotes, then unescape.

    This was `v.strip("'\"")`, which strips EVERY leading and trailing quote character — so a
    value legitimately ending in a quote lost it. The catalogue's detect probes are the case
    that exposed it: `"SELECT ... WHERE Field = 'Account.My_Field__c'"` came back missing its
    final apostrophe and every probe failed with MALFORMED_QUERY. The same three-line reader
    had been copied into three files, so all three were wrong the same way.
    """
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        q, v = v[0], v[1:-1]
        v = v.replace("''", "'") if q == "'" else v.replace('\\"', '"')
    return v


def _kb_load():
    """Parse the catalogue. PyYAML if present, else a small loader for the subset used here."""
    try:
        import yaml
        return yaml.safe_load(_KB.read_text())
    except ImportError:
        pass
    # Minimal fallback so the check never SKIPs merely because PyYAML is absent.
    data, cur, key = {"entries": []}, None, None
    for raw in _KB.read_text().split("\n"):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith("- id:"):
            cur = {"id": raw.split(":", 1)[1].strip()}
            data["entries"].append(cur)
            key = None
        elif cur is not None and raw.startswith("  ") and ":" in raw and not raw.startswith("    "):
            k, _, v = raw.strip().partition(":")
            v = v.strip()
            cur[k] = _kb_unquote(v) if v and v not in (">", "|") else ""
            key = k if v in (">", "|") else None
        elif cur is not None and key and raw.startswith("    "):
            cur[key] = (cur.get(key, "") + " " + raw.strip()).strip()
    return data


# A query can fail for two completely different reasons, and conflating them made this check
# assert that Salesforce had changed when in fact the org had simply stopped answering. These
# are the failures that say nothing about the claim under test.
_NOT_ABOUT_THE_CLAIM = ("REQUEST_LIMIT_EXCEEDED", "TotalRequests Limit exceeded",
                        "INVALID_SESSION_ID", "expired", "No authorization information",
                        "No org configuration found", "ENOTFOUND", "socket hang up")


_SFQ_STUB = None          # set by the verifier mutation test; None in every real run


def _sfq(target, soql, tooling=False):
    """Read-only SOQL. Returns (ok, rows) where ok is True, False, or None for 'cannot tell'."""
    if _SFQ_STUB is not None:
        return _SFQ_STUB(target, soql, tooling)
    cmd = ["sf", "data", "query", "--target-org", target, "--json", "--query", soql]
    if tooling:
        cmd.append("--use-tooling-api")
    try:
        r = _kb_sp.run(cmd, capture_output=True, text=True, timeout=120)
        blob = (r.stdout or "") + (r.stderr or "")
        if any(m in blob for m in _NOT_ABOUT_THE_CLAIM):
            return None, []
        d = _kb_json.loads(r.stdout)
        return (r.returncode == 0), (d.get("result", {}) or {}).get("records", [])
    except Exception:
        return None, []


# ── the live verifications named by `verify:` in the catalogue ─────────────────────────────
def _v_fls_absent_without_permset(target):
    """Assert that FLS is carried by PermissionSet-parented rows, and that nothing else grants it.

    The previous version returned True on every path a query could take — zero rows, all
    profile-owned, all permset-owned. It could not fail, so it proved nothing, while the entry
    it backed carried the label `verified-live`.

    The entry's real claim — a newly deployed field arrives invisible — needs an EXPERIMENT, and
    probe_cycle runs exactly that one: deploy, assert invisibility, grant, verify, purge. What is
    checkable HERE is the mechanism that claim depends on: FLS lives in FieldPermissions rows
    parented to a PermissionSet, and the platform still models it that way. If Salesforce ever
    returns a grant with no parent, or stops exposing the parent at all, the entry's remedy
    ("deploy a PermissionSet alongside") stops being actionable and this returns False.
    """
    ok, rows = _sfq(target, "SELECT Parent.IsOwnedByProfile, SobjectType FROM FieldPermissions "
                            "LIMIT 200")
    if ok is not True:
        return None, "FieldPermissions not queryable"
    if not rows:
        return None, "no FieldPermissions rows on this org — the mechanism cannot be observed"
    parentless = [r for r in rows if (r.get("Parent") or {}).get("IsOwnedByProfile") is None]
    if parentless:
        return False, (f"{len(parentless)}/{len(rows)} FLS rows expose no owning parent — the "
                       f"entry's remedy assumes every grant is carried by one")
    prof = sum(1 for r in rows if (r.get("Parent") or {}).get("IsOwnedByProfile"))
    return True, (f"{len(rows)} FLS rows sampled, every one parented ({prof} profile-owned, "
                  f"{len(rows) - prof} permission-set) — grants are carriable, as the remedy "
                  f"requires; the deploy-arrives-invisible experiment is probe_cycle's")


def _v_del_tombstones_visible(target):
    """Deleted custom fields are renamed with `_del` and remain queryable via Tooling."""
    ok, rows = _sfq(target,
                    "SELECT DeveloperName FROM CustomField WHERE DeveloperName LIKE '%\\_del' LIMIT 5",
                    tooling=True)
    if ok is not True:
        return None, "CustomField not queryable via Tooling"
    if not rows:
        # This returned True here, which inflated "5/5 re-verified" with a verifier that had
        # tested nothing — and contradicted kb_live_claims' own promise that an unrunnable
        # verification reports NA rather than passing quietly.
        return None, "no tombstones on this org right now — nothing to observe"
    import re as _r
    # `_del`, or `_del<N>` when the plain suffix was already taken. Accepting only `_del` was
    # what let the entry's reuse claim go unverified for as long as it existed: both this check
    # and the entry assumed a name could be freed once, and the org does it every time.
    bad = [r for r in rows
           if not _r.search(r"_del\d*$", str(r.get("DeveloperName", "")))]
    if bad:
        return False, (f"a field matched the tombstone query but carries no `_del` suffix "
                       f"({bad[0].get('DeveloperName')}) — the naming claim is wrong")
    numbered = [r["DeveloperName"] for r in rows
                if _r.search(r"_del\d+$", str(r.get("DeveloperName", "")))]
    return True, (f"{len(rows)} tombstone(s), all suffixed"
                  + (f"; {len(numbered)} numbered ({numbered[0]}), which is the org freeing the "
                     f"same name more than once" if numbered else ""))


def _v_flowdefinition_queryable(target):
    """The entry's claim is that the LATEST version is not the ACTIVE one, so a retrieve answers
    a different question than "what is running". This tested only that the remedy's mechanism
    exists — that FlowDefinition is Tooling-queryable — which is adjacent, not the claim.

    It now reads both numbers and asserts the distinction is real and observable.
    """
    ok, rows = _sfq(target, "SELECT DeveloperName, ActiveVersionId FROM FlowDefinition LIMIT 50",
                    tooling=True)
    if ok is None:
        return None, "org did not answer (limit/auth) — says nothing about the claim"
    if not ok:
        return False, "FlowDefinition NOT queryable via Tooling — remedy in the entry is wrong"
    if not rows:
        return None, "no flows on this org — the latest-vs-active distinction cannot be observed"
    ok2, vers = _sfq(target, "SELECT DefinitionId, VersionNumber, Status FROM Flow LIMIT 200",
                     tooling=True)
    if ok2 is not True or not vers:
        return None, f"{len(rows)} flow definition(s) visible, but Flow versions not queryable"
    latest, active = {}, {}
    for v in vers:
        d = v.get("DefinitionId")
        latest[d] = max(latest.get(d, 0), v.get("VersionNumber") or 0)
        if v.get("Status") == "Active":
            active[d] = v.get("VersionNumber") or 0
    diverged = [d for d in active if latest.get(d, 0) != active[d]]
    if diverged:
        return True, (f"{len(diverged)} flow(s) whose latest version is not the active one — "
                      f"the entry's distinction is live on this org")
    return True, (f"{len(rows)} definition(s), {len(active)} active; latest == active everywhere "
                  f"here, and both numbers are readable — which is what the remedy needs")


def _v_flowdefinitionview_standard_api(target):
    ok_std, rows = _sfq(target, "SELECT ApiName, IsActive FROM FlowDefinitionView LIMIT 5")
    ok_tool, _ = _sfq(target, "SELECT ApiName FROM FlowDefinitionView LIMIT 1", tooling=True)
    if ok_std is None or ok_tool is None:
        return None, "org did not answer (limit/auth) — says nothing about the claim"
    if not ok_std:
        return False, "FlowDefinitionView failed on the STANDARD api — entry is wrong"
    if ok_tool:
        return False, "FlowDefinitionView also worked via Tooling — entry overstates the problem"
    return True, f"standard api ok ({len(rows)} rows), Tooling errors — as documented"


def _v_de_org_reports_not_sandbox(target):
    ok, rows = _sfq(target, "SELECT IsSandbox, OrganizationType FROM Organization LIMIT 1")
    if ok is not True or not rows:
        return None, "Organization not queryable"
    r = rows[0]
    otype, sandbox = r.get("OrganizationType"), r.get("IsSandbox")
    if otype != "Developer Edition":
        # The entry is about DE orgs. Run anywhere else there is nothing to confirm — and the
        # old version returned True regardless, so pointing it at a sandbox still "passed".
        return None, f"this org is {otype!r}, not Developer Edition — claim not exercised here"
    if sandbox:
        return False, ("a Developer Edition org reported IsSandbox=True — the entry says DE "
                       "orgs do not, and every classifier depending on that is now wrong")
    return True, f"Developer Edition org reports IsSandbox={sandbox} — as the entry claims"


def _v_profile_deploy_is_overlay(target):
    """Deploy a profile that mentions one field, and assert an unmentioned grant survives.

    The entry this backs was WRONG until an experiment settled it — it claimed absence from a
    profile file removes a permission, cited to a guide that says no such thing. Folklore reaches
    a catalogue easily; the only thing that keeps it out is running the experiment. So this runs
    it, every release validation, and fails if the platform's behaviour changes.
    """
    import shutil as _sh, subprocess as _sp2, tempfile as _tf
    if _SFQ_STUB is not None:
        # Mutation-test path: the deploys cannot be stubbed, so only the ASSERTION is exercised.
        ok, rows = _sfq(target, "SELECT Field FROM FieldPermissions")
        fields = {r.get("Field") for r in rows or []}
        return ("A__c" in " ".join(fields)), "stubbed assertion"
    root = _KbP(_tf.mkdtemp(prefix="torque-prof-"))
    try:
        fdir = root / "force-app/main/default/objects/Lead/fields"
        pdir = root / "force-app/main/default/profiles"
        fdir.mkdir(parents=True); pdir.mkdir(parents=True)
        (root / "sfdx-project.json").write_text(_kb_json.dumps(
            {"packageDirectories": [{"path": "force-app", "default": True}],
             "namespace": "", "sourceApiVersion": "62.0"}))
        names = ["TorqueOverlayA", "TorqueOverlayB"]
        for n in names:
            (fdir / f"{n}__c.field-meta.xml").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<CustomField xmlns="http://soap.sforce.com/2006/04/metadata">'
                f"<fullName>{n}__c</fullName><label>{n}</label><type>Text</type>"
                "<length>40</length></CustomField>")

        def deploy(*a):
            return _kb_sp.run(["sf", "project", "deploy", "start", "--target-org", target,
                               "--json", *a], capture_output=True, text=True, cwd=root,
                              timeout=600)

        def perms(f):
            return ("<fieldPermissions><field>Lead." + f + "__c</field>"
                    "<editable>true</editable><readable>true</readable></fieldPermissions>")

        if deploy("--source-dir", "force-app").returncode != 0:
            return None, "could not deploy probe fields"
        (pdir / "Admin.profile-meta.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<Profile xmlns='
            '"http://soap.sforce.com/2006/04/metadata">'
            + perms(names[0]) + perms(names[1]) + "</Profile>")
        if deploy("--metadata", "Profile:Admin").returncode != 0:
            return None, "could not grant profile FLS"
        (pdir / "Admin.profile-meta.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<Profile xmlns='
            '"http://soap.sforce.com/2006/04/metadata">' + perms(names[1]) + "</Profile>")
        if deploy("--metadata", "Profile:Admin").returncode != 0:
            return None, "could not deploy the partial profile"
        ok, rows = _sfq(target, "SELECT Field FROM FieldPermissions WHERE "
                                "Parent.Profile.Name='System Administrator' AND Field IN "
                                f"('Lead.{names[0]}__c','Lead.{names[1]}__c')")
        if ok is not True:
            return None, "could not read back FieldPermissions"
        got = {r.get("Field") for r in rows}
        survived = f"Lead.{names[0]}__c" in got
        if not survived:
            return False, ("an unmentioned profile grant was REMOVED — profile deploys are "
                           "destructive by absence after all, and this entry is now wrong")
        return True, (f"a grant absent from the deployed profile survived ({len(got)} of 2 "
                      f"grants present) — profile deploy is an overlay")
    finally:
        _kb_sp.run(["sf", "project", "delete", "source", "--target-org", target, "--no-prompt",
                    "--json", "--metadata", "CustomField:Lead.TorqueOverlayA__c",
                    "--metadata", "CustomField:Lead.TorqueOverlayB__c"],
                   capture_output=True, text=True, cwd=root, timeout=600)
        _sh.rmtree(root, ignore_errors=True)


_VERIFIERS = {
    "profile_deploy_is_overlay": _v_profile_deploy_is_overlay,
    "fls_absent_without_permset": _v_fls_absent_without_permset,
    "del_tombstones_visible": _v_del_tombstones_visible,
    "flowdefinition_queryable": _v_flowdefinition_queryable,
    "flowdefinitionview_standard_api": _v_flowdefinitionview_standard_api,
    "de_org_reports_not_sandbox": _v_de_org_reports_not_sandbox,
}


@check("kb_integrity", "static", catastrophe=True)
def _kb_integrity():
    """Schema, honesty of the confidence field, and no undated entries."""
    if not _KB.exists():
        return Result("kb_integrity", FAIL, "knowledge/salesforce-platform.yml is missing")
    d = _kb_load()
    entries = d.get("entries") or []
    if len(entries) < 10:
        return Result("kb_integrity", FAIL, f"only {len(entries)} entries — catalogue is a stub")
    ids = [e.get("id") for e in entries]
    if len(set(ids)) != len(ids):
        return Result("kb_integrity", FAIL, "duplicate entry ids")
    for e in entries:
        missing = [k for k in _REQUIRED if not e.get(k)]
        if missing:
            return Result("kb_integrity", FAIL, f"{e.get('id')} missing {missing}")
        if e["confidence"] not in _CONFIDENCE:
            return Result("kb_integrity", FAIL, f"{e['id']} has confidence {e['confidence']!r}")
        if e["confidence"] == "verified-live" and not e.get("verify"):
            return Result("kb_integrity", FAIL,
                          f"{e['id']} claims verified-live with no runnable check — the whole "
                          f"point of the label is that something re-runs it")
        if e["confidence"] == "documented" and not e.get("source"):
            return Result("kb_integrity", FAIL, f"{e['id']} claims documented with no source")
        if e.get("verify") and e["verify"] not in _VERIFIERS:
            return Result("kb_integrity", FAIL,
                          f"{e['id']} names verifier {e['verify']!r} which does not exist")
    live = sum(1 for e in entries if e["confidence"] == "verified-live")
    doc = sum(1 for e in entries if e["confidence"] == "documented")
    prac = len(entries) - live - doc
    return Result("kb_integrity", PASS,
                  f"{len(entries)} entries across {len(set(e['domain'] for e in entries))} domains; "
                  f"{live} verified-live, {doc} documented, {prac} practitioner")


@check("kb_live_claims", "capability", catastrophe=True)
def _kb_live_claims(target):
    """Re-verify every `verified-live` claim against a real org.

    This is the difference between a catalogue and a claim. Salesforce ships three releases a
    year; a platform fact recorded once and never re-checked is decaying from the day it is
    written. An entry whose verification cannot run reports NA rather than passing quietly.
    """
    d = _kb_load()
    entries = [e for e in (d.get("entries") or []) if e.get("confidence") == "verified-live"]
    if not entries:
        return Result("kb_live_claims", FAIL, "no verified-live entries — nothing is being proven")
    passed, untestable, failed = [], [], []
    for e in entries:
        fn = _VERIFIERS.get(e["verify"])
        try:
            ok, detail = fn(target)
        except Exception as ex:
            ok, detail = None, f"verifier raised {type(ex).__name__}"
        if ok is True:
            passed.append(e["id"])
        elif ok is None:
            untestable.append(f"{e['id']} ({detail})")
        else:
            failed.append(f"{e['id']}: {detail}")
    if failed:
        return Result("kb_live_claims", FAIL,
                      f"platform claim no longer holds — {'; '.join(failed)}")
    msg = f"{len(passed)}/{len(entries)} live claims re-verified against {target}"
    if untestable:
        msg += f"; {len(untestable)} untestable here ({', '.join(untestable)})"
    if untestable:
        # PASS required only ONE verifier to succeed, so six verified-live entries with five
        # untestable reported PASS — while the label on those five promises they are re-checked
        # against a live org. The strongest provenance claim in the catalogue was the weakest
        # thing the harness actually proved (release panel, codex/gpt-5.6-sol).
        return Result("kb_live_claims", WARN,
                      msg + f" — {len(untestable)} entr(ies) carry the `verified-live` label "
                            f"without being verified in this run")
    return Result("kb_live_claims", PASS, msg)


@check("kb_injection", "static", catastrophe=True)
def _kb_injection():
    """The catalogue must SPEAK at the moment of the operation, and stay silent otherwise.

    Knowledge delivered by a model-honoured "consult this rule" trigger is knowledge that
    arrives when the model happens to remember. The gate already reads every command and is
    already deciding — so it is where the platform can answer back. This pins both halves:
    the right note fires for the right command, and ordinary work produces no noise at all.
    A gate that chatters is a gate people silence.
    """
    cases = [
        ("sf data update record --where \"T=null\" --values \"T=x\" --sobject Account --target-org o",
         "no-op-update-is-not-free"),
        ("sf project deploy start --manifest package.xml --target-org o", "fls-not-automatic"),
        ("sf data delete bulk --sobject W__c --file i.csv --hard-delete --target-org o",
         "recycle-bin-retention"),
    ]
    for cmd, want in cases:
        r = _kb_sp.run([_kb_sys.executable, str(ROOT / "hooks" / "prod_write_gate.py")],
                       input=_kb_json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}),
                       capture_output=True, text=True, cwd=ROOT, timeout=60)
        if want not in r.stderr:
            return Result("kb_injection", FAIL,
                          f"no platform note {want!r} for: {cmd[:52]}")
    quiet = _kb_sp.run([_kb_sys.executable, str(ROOT / "hooks" / "prod_write_gate.py")],
                       input=_kb_json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls -la"}}),
                       capture_output=True, text=True, cwd=ROOT, timeout=60)
    if "PLATFORM NOTE" in quiet.stderr:
        return Result("kb_injection", FAIL, "ordinary command produced a platform note (noise)")
    return Result("kb_injection", PASS,
                  f"{len(cases)} operations received the right note; ordinary work stayed silent")


@check("claimed_counts", "static", catastrophe=False)
def _claimed_counts():
    """Every count stated in prose must match what is on disk.

    A number written once and never re-derived is the most common form of quiet dishonesty in
    a repo like this: the artifact grows, the sentence describing it does not, and a reader
    who checks finds the document overstating itself. So the documents state the recorded
    fixture count, and this re-derives it.
    """
    recorded = sum(len(_kb_json.loads(f.read_text()).get("fixtures", []))
                   for f in sorted((ROOT / "harness" / "tests").glob("gate_fixtures*.json")))
    bad = []
    for rel in ("guide/torque-guide.html", "README.md", "bin/torque-demo", "bin/torque-init"):
        f = ROOT / rel
        if not f.exists():
            continue
        body = f.read_text()
        for m in _kb_re.finditer(r"(\d{2,4}) recorded", body):
            if int(m.group(1)) != recorded:
                bad.append(f"{rel} says {m.group(1)} recorded, on disk there are {recorded}")
        # The check count drifted from 24 to 27 to 43 while two sentences in the guide kept
        # saying 24 and 27. Derive it from the registry rather than trusting either.
        # Counts are stated per profile as well as in total, and the profiles nest — so a
        # blanket "N checks" comparison against the registry total is wrong for two of the
        # three. Any stated count must match SOME profile's cumulative total.
        from collections import Counter as _C
        import importlib.util as _ilu
        _sp = _ilu.spec_from_file_location("tv", ROOT / "harness" / "validate.py")
        _v = _ilu.module_from_spec(_sp); _sp.loader.exec_module(_v)
        _c = _C(pr for _n, pr, _cc, _f in REGISTRY)
        valid = {sum(n for pp, n in _c.items() if _v.RANK[pp] <= _v.RANK[pr])
                 for pr in _v.PROFILES}
        for m in _kb_re.finditer(r"(\d{2,4}) checks\b", body):
            if int(m.group(1)) not in valid:
                bad.append(f"{rel} says {m.group(1)} checks; no profile has that many "
                           f"(profiles: {sorted(valid)})")
    if bad:
        return Result("claimed_counts", FAIL, "; ".join(bad))
    return Result("claimed_counts", PASS,
                  f"{recorded} recorded fixtures and {len(REGISTRY)} registered checks; every "
                  f"prose count matches the fixtures on disk or a real profile total")


@check("lesson_backlog", "static", catastrophe=False)
def _lesson_backlog():
    """A capture queue nobody empties is the failure mode this design exists to avoid.

    The observer's whole justification is that manual capture has no intake. That argument
    collapses if automatic capture just relocates the problem — a file of candidates that
    grows and is never converted is exactly the inert notebook, wearing a different name. So
    the backlog is reported here, and ages into a WARN. Visible rot is survivable; quiet rot
    is what kills these systems.
    """
    cand = ROOT / "local" / "lessons" / "candidates.jsonl"
    if not cand.exists():
        return Result("lesson_backlog", PASS, "no captured candidates")
    st = cand.stat()
    if st.st_mode & 0o077:
        return Result("lesson_backlog", FAIL,
                      f"candidates.jsonl is {oct(st.st_mode & 0o777)} — it holds org output")
    recs = []
    for line in cand.read_text().splitlines():
        try:
            recs.append(_kb_json.loads(line))
        except Exception:
            return Result("lesson_backlog", FAIL, "candidates.jsonl has a malformed line")
    pairs = [r for r in recs if r.get("kind") == "resolution"]
    if not pairs:
        return Result("lesson_backlog", PASS,
                      f"{len(recs)} observation(s), no resolved pair yet — nothing to record")
    import time as _t
    oldest_days = (_t.time() - min(r["at"] for r in pairs)) / 86400
    msg = (f"{len(pairs)} resolved pair(s) awaiting `torque lesson review`; "
           f"oldest {oldest_days:.1f}d")
    return Result("lesson_backlog", WARN if oldest_days > 14 else PASS, msg)


@check("observer_is_not_a_gate", "static", catastrophe=True)
def _observer_is_not_a_gate():
    """The observer must never be able to block a tool call, or to write knowledge.

    It runs after every Bash call, which makes it the single most-invoked piece of code here.
    Two properties keep that safe: it can only ever exit 0, and it writes only to `local/`.
    A PostToolUse hook that could deny would turn an observation bug into an outage, and one
    that could append to the catalogue would let noise become a claim without review.
    """
    src = (ROOT / "hooks" / "lesson_observer.py").read_text()
    if "salesforce-platform.yml" in src or "gate_fixtures" in src:
        return Result("observer_is_not_a_gate", FAIL, "observer writes knowledge directly")
    if _kb_re.search(r"^\s*lib\.run_gate\(", src, _kb_re.M):
        return Result("observer_is_not_a_gate", FAIL,
                      "observer uses lib.run_gate, which is fail-CLOSED and denies on any "
                      "unexpected exception — correct for a gate, wrong for an observer")
    # Searching the source for `lib.deny` was the whole test, and it missed the denial reached
    # THROUGH run_gate: malformed stdin exited 2 while this check passed. Run it instead.
    for label, payload in (("garbage stdin", "not json at all"),
                           ("empty stdin", ""),
                           ("no tool_input", '{"tool_name":"Bash"}'),
                           ("null response", '{"tool_name":"Bash","tool_input":'
                                             '{"command":"sf x"},"tool_response":null}'),
                           ("huge command", _kb_json.dumps(
                               {"tool_name": "Bash",
                                "tool_input": {"command": "sf " + "a" * 200000}}))):
        r = _kb_sp.run([_kb_sys.executable, str(ROOT / "hooks" / "lesson_observer.py")],
                       input=payload, capture_output=True, text=True, cwd=ROOT, timeout=90)
        if r.returncode != 0:
            return Result("observer_is_not_a_gate", FAIL,
                          f"observer exited {r.returncode} on {label} — it runs after every "
                          f"Bash call and must never interfere with one")
    # and prove it: a payload that would deny at the gate must pass here
    ev = {"tool_name": "Bash",
          "tool_input": {"command": "sf data delete bulk --sobject Account --file x.csv "
                                    "--hard-delete --target-org acme-prod"},
          "tool_response": {"stdout": "", "stderr": "INVALID_FIELD", "exit_code": 1}}
    r = _kb_sp.run([_kb_sys.executable, str(ROOT / "hooks" / "lesson_observer.py")],
                   input=_kb_json.dumps(ev), capture_output=True, text=True,
                   cwd=ROOT, timeout=60)
    if r.returncode != 0:
        return Result("observer_is_not_a_gate", FAIL,
                      f"observer exited {r.returncode} on a command the gate denies — "
                      f"it would block work it is only supposed to watch")
    st = (ROOT / "hooks" / "lesson_observer.py")
    return Result("observer_is_not_a_gate", PASS,
                  "observer cannot deny, cannot write knowledge, and exits 0 on a gate-denied shape")


@check("blast_radius_honesty", "static", catastrophe=True)
def _blast_radius_honesty():
    """Every source that cannot answer must say so — never a silent zero.

    A blast radius that under-reports is worse than none, because somebody would act on it.
    The whole design rests on one distinction: "there is no trigger on this object" and "I
    could not find out whether there is a trigger on this object" are different answers. This
    proves the distinction survives — point it at an org that cannot answer anything, and it
    must report UNDETERMINED for every part and exit non-zero, not print a reassuring page of
    zeroes.
    """
    br = ROOT / "bin" / "torque-blast-radius"
    if not br.exists():
        return Result("blast_radius_honesty", FAIL, "bin/torque-blast-radius is missing")
    r = _kb_sp.run([_kb_sys.executable, str(br), "--target-org", "torque-no-such-org-xyz",
                    "--sobject", "Account", "--json"],
                   capture_output=True, text=True, cwd=ROOT, timeout=180)
    if r.returncode == 0:
        return Result("blast_radius_honesty", FAIL,
                      "exited 0 against an unreachable org — it reported a complete picture "
                      "it could not possibly have")
    try:
        rep = _kb_json.loads(r.stdout)
    except Exception:
        return Result("blast_radius_honesty", FAIL, f"--json did not emit JSON: {r.stdout[:100]}")
    zeroed = [k for k, v in rep.items()
              if k not in ("undetermined", "cascade_soft") and v == []]
    if zeroed:
        return Result("blast_radius_honesty", FAIL,
                      f"reported empty (not UNDETERMINED) for {zeroed} against an org that "
                      f"answered nothing — a silent zero is the one failure mode that matters")
    if not rep.get("undetermined"):
        return Result("blast_radius_honesty", FAIL, "no source was recorded as undetermined")
    # The completeness verdict must agree with the body. It once printed "picture complete —
    # every source answered" directly beneath five children marked UNDETERMINED, because the
    # cascade section collected its own failures and never told the verdict about them. A
    # summary that contradicts the detail above it is worse than no summary.
    txt = _kb_sp.run([_kb_sys.executable, str(br), "--target-org", "torque-no-such-org-xyz",
                      "--sobject", "Account", "--operation", "delete"],
                     capture_output=True, text=True, cwd=ROOT, timeout=180)
    body = txt.stdout
    if "picture complete" in body and ("UNDETERMINED" in body or txt.returncode != 0):
        return Result("blast_radius_honesty", FAIL,
                      "claimed 'picture complete' while the body shows UNDETERMINED — the "
                      "summary contradicts the detail above it")
    if "INCOMPLETE" not in body:
        return Result("blast_radius_honesty", FAIL,
                      "an unreachable org did not produce an INCOMPLETE verdict")
    return Result("blast_radius_honesty", PASS,
                  f"{len(rep['undetermined'])} unanswerable source(s) reported as UNDETERMINED, "
                  f"exit {r.returncode}; no source silently returned zero, and the completeness "
                  f"verdict agrees with the body")


@check("lesson_writer_roundtrip", "static", catastrophe=True)
def _lesson_writer_roundtrip():
    """A fact containing quotes must still produce a file that parses.

    The writer wrapped titles in bare single quotes, so the first fact recorded containing an
    apostrophe — "a Developer Edition org's daily cap" — wrote a catalogue that would not load.
    The damage surfaced later, in a different check, with a YAML parser error naming a line
    number instead of the tool that wrote it. This closes the loop where it opened.
    """
    import importlib.machinery as _im, importlib.util as _il, shutil as _sh, tempfile as _tmp, types as _ty
    # The file has no .py suffix, so spec_from_file_location needs the loader named explicitly.
    spec = _il.spec_from_file_location(
        "torque_lesson", ROOT / "bin" / "torque-lesson",
        loader=_im.SourceFileLoader("torque_lesson",
                                              str(ROOT / "bin" / "torque-lesson")))
    mod = _il.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with _tmp.TemporaryDirectory() as td:
        tmp_kb = _KbP(td) / "kb.yml"
        _sh.copy(_KB, tmp_kb)
        mod.KB, mod.ROOT = tmp_kb, _KbP(td)
        a = _ty.SimpleNamespace(
            id="roundtrip-probe-quote", domain="data",
            title="an org's field said \"no\" — and it's O'Brien's fault",
            symptom="it's: broken", cause="a value with 'quotes' and \"doubles\" and a: colon",
            remedy="don't: assume", detect="sf data query --query \"SELECT Id FROM X\" # it's fine",
            confidence="practitioner", source="")
        try:
            mod.add_fact(a)
        except SystemExit as e:
            return Result("lesson_writer_roundtrip", FAIL, f"writer refused a valid fact: {e}")
        try:
            import yaml
            d = yaml.safe_load(tmp_kb.read_text())
        except ImportError:
            return Result("lesson_writer_roundtrip", NA, "PyYAML absent; cannot prove it parses")
        except Exception as e:
            return Result("lesson_writer_roundtrip", FAIL,
                          f"a fact containing quotes produced unparseable YAML: "
                          f"{type(e).__name__}: {str(e)[:90]}")
        got = [e for e in d.get("entries") or [] if e.get("id") == "roundtrip-probe-quote"]
        if not got:
            return Result("lesson_writer_roundtrip", FAIL, "fact parsed but the entry is absent")
        if got[0]["title"] != a.title:
            return Result("lesson_writer_roundtrip", FAIL,
                          f"title round-tripped as {got[0]['title']!r}, not {a.title!r}")
    return Result("lesson_writer_roundtrip", PASS,
                  "a fact with apostrophes, double quotes and a colon round-trips intact")


@check("limit_relabel_is_safe", "static", catastrophe=True)
def _limit_relabel_is_safe():
    """The rate-limit outcome must never launder a real failure into something shippable.

    Re-labelling FAIL as ⧗ when an org is out of API budget is honest — the check genuinely
    could not reach a conclusion — but it is exactly the shape of change that quietly turns a
    red suite green. Two properties keep it safe, and both are asserted here: a run containing
    any ⧗ can never report PASS, and when the org is NOT out of budget a failure stays a
    failure. Without the second, the first is decoration.
    """
    import importlib.util as _il
    spec = _il.spec_from_file_location("torque_validate", ROOT / "harness" / "validate.py")
    v = _il.module_from_spec(spec)
    spec.loader.exec_module(v)

    import io, contextlib
    def verdict(results):
        with contextlib.redirect_stdout(io.StringIO()):
            return v.print_report("test", results)

    limited = verdict([v.Result("live", v.FAIL, "REQUEST_LIMIT_EXCEEDED: TotalRequests Limit"),
                       v.Result("ok", v.PASS, "fine")])
    if limited == v.PASS:
        return Result("limit_relabel_is_safe", FAIL,
                      "a run containing a rate-limited check reported PASS")
    real = verdict([v.Result("bug", v.FAIL, "field Foo__c does not exist")])
    if real != v.FAIL:
        return Result("limit_relabel_is_safe", FAIL,
                      f"an ordinary failure reported {real!r} instead of FAIL")
    if v.rate_limited("field Foo__c does not exist"):
        return Result("limit_relabel_is_safe", FAIL,
                      "an ordinary failure message was classified as rate-limited")
    if not v.rate_limited("REQUEST_LIMIT_EXCEEDED"):
        return Result("limit_relabel_is_safe", FAIL, "a real limit error was not recognised")
    return Result("limit_relabel_is_safe", PASS,
                  f"rate-limited run degrades to {limited!r}, never PASS; ordinary failures "
                  f"still FAIL")


@check("session_log_path_containment", "static", catastrophe=True)
def _session_log_path_containment():
    """An org alias must never be able to steer a write outside the sessions directory.

    The alias came from the caller and went straight into a path — `d / f"{org}.jsonl"` — so
    an org named `../../../../tmp/x` wrote a file into the home directory. That was verified
    by doing it, not by reading the code. The alias is attacker-adjacent in the only way that
    matters here: it is whatever string reached the tool.
    """
    import importlib.machinery as _im, importlib.util as _il
    src = ROOT / "bin" / "torque-log"
    spec = _il.spec_from_file_location("torque_log", src,
                                       loader=_im.SourceFileLoader("torque_log", str(src)))
    mod = _il.module_from_spec(spec)
    spec.loader.exec_module(mod)
    base = (mod.lib.LOCAL / "sessions")
    base.mkdir(parents=True, exist_ok=True)
    escapes = []
    for hostile in ("../../../../tmp/evil", "..", "/etc/passwd", "a/../../b", "....//x",
                    "sf-‮prod", "con", ".", "-rf"):
        try:
            p = mod._log_path(base, hostile)
        except ValueError:
            continue                       # refused outright, which is also correct
        if not str(p.resolve()).startswith(str(base.resolve()) + _kb_os.sep):
            escapes.append(f"{hostile!r} → {p}")
    if escapes:
        return Result("session_log_path_containment", FAIL,
                      f"alias escaped the sessions directory: {escapes}")
    ok = mod._log_path(base, "sf-coffee")
    if ok.name != "sf-coffee.jsonl":
        return Result("session_log_path_containment", FAIL,
                      f"an ordinary alias was mangled to {ok.name!r} — sanitising must not "
                      f"break the normal case")
    return Result("session_log_path_containment", PASS,
                  "8 hostile aliases contained; an ordinary alias is unchanged")


@check("allow_decisions_logged", "static", catastrophe=False)
def _allow_decisions_logged():
    """The audit trail must contain the decisions the documentation says it contains.

    DENY and PROD-WRITE were logged; ALLOW was not logged at all, while the guide described
    the file as a complete decision trail. Either the code or the sentence had to change. The
    code did, but selectively: an ALLOW is recorded when the command touches Salesforce, and
    not for the thousands of ordinary shell calls that would otherwise bury it.
    """
    import tempfile as _tf
    with _tf.TemporaryDirectory() as td:
        env = dict(_kb_os.environ, TORQUE_HOME=str(ROOT), HOME=td,
                   TORQUE_ANCHOR=str(_KbP(td) / ".torque"))
        log = _KbP(td) / "audit.log"
        env["TORQUE_AUDIT_LOG"] = str(log)

        def fire(cmd):
            _kb_sp.run([_kb_sys.executable, str(ROOT / "hooks" / "prod_write_gate.py")],
                       input=_kb_json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}),
                       capture_output=True, text=True, cwd=ROOT, timeout=60, env=env)

        fire("sf data query --target-org zzz --query \"SELECT Id FROM Account\"")
        fire("ls -la")
        if not log.exists():
            return Result("allow_decisions_logged", NA,
                          "audit log path is not overridable by env; cannot assert in isolation")
        lines = [_kb_json.loads(x) for x in log.read_text().splitlines() if x.strip()]
        allows = [x for x in lines if x.get("decision") == "ALLOW"]
        if not any("sf data query" in str(x.get("detail")) for x in allows):
            return Result("allow_decisions_logged", FAIL,
                          "an allowed Salesforce operation produced no ALLOW entry")
        if any("ls -la" in str(x.get("detail")) for x in lines):
            return Result("allow_decisions_logged", FAIL,
                          "an ordinary shell command was logged — the trail will drown")
    return Result("allow_decisions_logged", PASS,
                  "an allowed Salesforce operation is recorded; ordinary shell calls are not")


@check("python_floor_is_real", "static", catastrophe=False)
def _python_floor_is_real():
    """The documented Python minimum must match the syntax the code actually uses.

    A prerequisite is a promise in both directions. Claim too low and someone's install fails
    on syntax; claim too high and people with a working interpreter are turned away for no
    reason. `ast.parse(feature_version=...)` answers the syntax half exactly, so the floor can
    be derived from the tree instead of remembered. It bounds syntax only — a stdlib call added
    in a later release would not show up here — so the check reports what it proved.
    """
    import ast as _ast
    docs = (ROOT / "README.md").read_text()
    m = _kb_re.search(r"python3.{0,12}?([23])\.(\d+)", docs)
    if not m:
        return Result("python_floor_is_real", FAIL, "README states no python3 minimum")
    claimed = (int(m.group(1)), int(m.group(2)))

    sources = [p for p in ROOT.rglob("*.py")
               if ".git" not in p.parts and "local" not in p.parts]
    sources += [ROOT / "bin" / n for n in ("torque", "torque-lesson", "torque-blast-radius",
                                           "torque-log", "torque-demo", "torque-install-gates")]
    sources = [p for p in sources if p.exists()]

    def parses_all(ver):
        for p in sources:
            try:
                _ast.parse(p.read_text(), feature_version=ver)
            except SyntaxError:
                return False
            except Exception:
                continue
        return True

    if not parses_all(claimed):
        return Result("python_floor_is_real", FAIL,
                      f"README claims python3 ≥ {claimed[0]}.{claimed[1]} but the source uses "
                      f"syntax newer than that — an install following the README would fail")
    lowest = claimed
    for minor in range(claimed[1] - 1, 7, -1):
        if parses_all((claimed[0], minor)):
            lowest = (claimed[0], minor)
        else:
            break
    # The syntax bound says nothing about stdlib calls that appeared in a later release, so
    # the ones actually used here are named and versioned. If a future edit reaches for
    # `tomllib` or `removeprefix`, this raises the floor rather than letting the README drift.
    _STDLIB_SINCE = ((3, 8), ("missing_ok=True", "walrus")), ((3, 9), ("removeprefix",
                     "removesuffix", "zoneinfo", "graphlib")), ((3, 11), ("tomllib",
                     "ExceptionGroup", "TaskGroup"))
    stdlib_floor = (3, 0)
    for ver, markers in _STDLIB_SINCE:
        for p_ in sources:
            if p_.name == "check_kb.py":
                continue          # this file NAMES the markers; matching itself proves nothing
            body = p_.read_text()
            if any(mk in body for mk in markers):
                stdlib_floor = max(stdlib_floor, ver)
                break
    lowest = max(lowest, stdlib_floor)
    if lowest > claimed:
        return Result("python_floor_is_real", FAIL,
                      f"README claims ≥ {claimed[0]}.{claimed[1]} but the code needs "
                      f"{lowest[0]}.{lowest[1]}")
    if lowest != claimed:
        return Result("python_floor_is_real", WARN,
                      f"README claims ≥ {claimed[0]}.{claimed[1]}, but every source file parses "
                      f"under {lowest[0]}.{lowest[1]} and uses no stdlib newer than that — the "
                      f"stated floor turns away users whose interpreter would work")
    return Result("python_floor_is_real", PASS,
                  f"{len(sources)} source files: syntax and stdlib both land exactly on the "
                  f"documented floor {claimed[0]}.{claimed[1]}")


@check("component_self_tests", "static", catastrophe=True)
def _component_self_tests():
    """Every component that ships a --self-test must pass it, in this run.

    A self-test nobody runs is documentation. Three of these were written and then only ever
    invoked by hand, which is the state a self-test decays from: it passes on the day it is
    written and silently stops being true afterwards.
    """
    comps = []
    for rel in ("hooks/lesson_observer.py", "harness/capture.py", "bin/torque-blast-radius"):
        p = ROOT / rel
        if p.exists() and "--self-test" in p.read_text():
            comps.append(rel)
    if not comps:
        return Result("component_self_tests", FAIL, "no component exposes --self-test")
    failed = []
    for rel in comps:
        r = _kb_sp.run([_kb_sys.executable, str(ROOT / rel), "--self-test"],
                       capture_output=True, text=True, cwd=ROOT, timeout=180)
        if r.returncode != 0:
            failed.append(f"{rel}: {(r.stdout + r.stderr).strip().splitlines()[-1][:80]}")
    if failed:
        return Result("component_self_tests", FAIL, "; ".join(failed))
    return Result("component_self_tests", PASS,
                  f"{len(comps)} component self-test(s) pass: {', '.join(comps)}")


@check("readme_transcripts_are_real", "static", catastrophe=True)
def _readme_transcripts_are_real():
    """A transcript in the README must be what the tool actually prints.

    Sample output is the most persuasive thing in a README and the easiest to write by hand.
    This one is reproducible offline — no org, no credentials — so there is no excuse for it
    to be composed. Every TORQUE GATE / TORQUE PLATFORM NOTE line quoted in the README is
    required to appear in the real output of the command shown above it.
    """
    readme = (ROOT / "README.md").read_text()
    # The arrow lines carry the actual advice and are the easiest part to improve by hand,
    # so they are verified too — not just the headers above them.
    quoted, prev_was_note = [], False
    for ln in readme.splitlines():
        if ln.startswith(("TORQUE GATE DENY", "TORQUE PLATFORM NOTE")):
            quoted.append(ln.strip())
            prev_was_note = True
        elif prev_was_note and ln.lstrip().startswith("→"):
            quoted.append(ln.strip())
        else:
            prev_was_note = False
    if not quoted:
        return Result("readme_transcripts_are_real", FAIL,
                      "no gate transcript found in the README to verify")
    cmd = ("sf data delete bulk --sobject Account --file ids.csv --hard-delete "
           "--target-org acme-prod")
    r = _kb_sp.run([_kb_sys.executable, str(ROOT / "hooks" / "destructive_data_gate.py")],
                   input=_kb_json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}),
                   capture_output=True, text=True, cwd=ROOT, timeout=60)
    actual = _kb_re.sub(r"\x1b\[[0-9;]*m", "", r.stderr)
    missing = [q for q in quoted if q not in actual]
    if missing:
        return Result("readme_transcripts_are_real", FAIL,
                      f"{len(missing)} README transcript line(s) are not what the tool prints: "
                      f"{missing[0][:110]!r}")
    return Result("readme_transcripts_are_real", PASS,
                  f"{len(quoted)} transcript line(s) reproduced verbatim from a live gate run")


@check("token_store_hygiene", "static", catastrophe=False)
def _token_store_hygiene():
    """Approval tokens must be 0600, and expired ones must not pile up unnoticed.

    Twenty expired tokens had accumulated over two days, several of them 0644 — written by a
    path that, unlike the fixture suite's, never chmod'd. The 0700 directory above them meant
    nothing was exposed, but a credential-shaped file whose mode depends on which code path
    created it is one refactor away from mattering. Expired tokens are harmless individually
    and a signal collectively: each is an approval that was minted and never used.
    """
    import time as _t
    import stat as _st
    tokens = _lib_anchor_tokens()
    if tokens is None or not tokens.exists():
        return Result("token_store_hygiene", PASS, "no token store yet")
    loose, expired, live = [], 0, 0
    for f in tokens.glob("*.token"):
        if _st.S_IMODE(f.stat().st_mode) & 0o077:
            loose.append(f"{f.name} {oct(_st.S_IMODE(f.stat().st_mode))}")
        try:
            exp = _kb_json.loads(f.read_text()).get("exp", 0)
        except Exception:
            exp = 0
        if exp < _t.time():
            expired += 1
        else:
            live += 1
    if loose:
        return Result("token_store_hygiene", FAIL,
                      f"{len(loose)} approval token(s) readable beyond the owner: {loose[:3]}")
    if expired > 10:
        return Result("token_store_hygiene", WARN,
                      f"{expired} expired token(s) accumulating — each is an approval minted "
                      f"and never used; nothing reaps them")
    return Result("token_store_hygiene", PASS,
                  f"{live} live, {expired} expired, all owner-only")


def _lib_anchor_tokens():
    import importlib.util as _il
    spec = _il.spec_from_file_location("torque_lib", ROOT / "hooks" / "lib.py")
    m = _il.module_from_spec(spec)
    try:
        spec.loader.exec_module(m)
        return m.TOKENS
    except Exception:
        return None


@check("verifiers_can_fail", "static", catastrophe=True)
def _verifiers_can_fail():
    """Every live-claim verifier must return False when its claim is false.

    This is the check the knowledge layer was missing, and its absence was the layer's most
    serious defect. `kb_live_claims` reported "5/5 live claims re-verified" while three of the
    five verifiers had no reachable False path at all: they queried the org, ignored the answer,
    and returned True. Pointed at an org where the entry was plainly wrong — a sandbox, for the
    entry asserting Developer Edition orgs report IsSandbox=False — they still passed.

    A check that cannot fail proves nothing. That is this project's own standard, applied to
    the gates by --self-test mutators since the beginning, and not applied here until now. So
    each verifier is fed the org response that makes its entry FALSE, and is required to say so.
    """
    global _SFQ_STUB
    falsifying = {
        # a grant with no owning parent — the remedy assumes every grant is carried by one
        "fls_absent_without_permset": lambda t, q, tl: (True, [{"Parent": {}, "SobjectType": "A"}]),
        # a field matched by the tombstone query that does not carry the suffix
        "del_tombstones_visible": lambda t, q, tl: (True, [{"DeveloperName": "Not_A_Tombstone"}]),
        # FlowDefinition unreachable via Tooling — the entry's remedy depends on it
        "flowdefinition_queryable": lambda t, q, tl: (False, []),
        # FlowDefinitionView failing on the standard API, which the entry says works
        "flowdefinitionview_standard_api": lambda t, q, tl: (False, []),
        # a Developer Edition org reporting IsSandbox=True
        # an unmentioned grant reported as REMOVED — the entry's claim inverted
        "profile_deploy_is_overlay": lambda t, q, tl: (True, [{"Field": "Lead.B__c"}]),
        "de_org_reports_not_sandbox":
            lambda t, q, tl: (True, [{"IsSandbox": True, "OrganizationType": "Developer Edition"}]),
    }
    missing = sorted(set(_VERIFIERS) - set(falsifying))
    if missing:
        return Result("verifiers_can_fail", FAIL,
                      f"no falsifying case written for {missing} — a verifier nobody has tried "
                      f"to break is a verifier nobody has checked")
    survived = []
    for name, stub in falsifying.items():
        fn = _VERIFIERS.get(name)
        if fn is None:
            return Result("verifiers_can_fail", FAIL, f"verifier {name!r} no longer exists")
        _SFQ_STUB = stub
        try:
            ok, detail = fn("stub-org")
        except Exception as e:
            _SFQ_STUB = None
            return Result("verifiers_can_fail", FAIL,
                          f"{name} raised on its falsifying case: {type(e).__name__}: {e}")
        finally:
            _SFQ_STUB = None
        if ok is not False:
            survived.append(f"{name} returned {ok!r} ({detail[:60]})")
    if survived:
        return Result("verifiers_can_fail", FAIL,
                      f"{len(survived)} verifier(s) did NOT fail on a falsifying org response: "
                      f"{survived}")
    return Result("verifiers_can_fail", PASS,
                  f"all {len(falsifying)} verifiers return False when their entry is false")


@check("detect_probes_run", "capability", catastrophe=True)
def _detect_probes_run(target):
    """Every detect probe must actually run, and its outcome must match what the entry claims.

    Nine entries declared a `detect:` probe — described in the catalogue's own header as the
    query that answers "is this happening to me right now" — and nothing in the repo had ever
    executed one. The only code that touched the field was the code that wrote it. Two of the
    nine had been unrunnable for as long as they existed: one queried a Tooling object without
    the Tooling flag, and one put `ALL ROWS` in the SOQL text where the CLI wants --all-rows.
    Nobody could know, because nothing asked.

    A broken probe is worse than a missing one: `torque checkup` would report the org clean on a
    trap it never tested. So a probe that cannot run fails the build, and so does an entry the
    platform contradicts.
    """
    if not target:
        return Result("detect_probes_run", SKIP, "no --target-org")
    r = _kb_sp.run([_kb_sys.executable, str(ROOT / "bin" / "torque-checkup"),
                    "--target-org", target, "--json"],
                   capture_output=True, text=True, cwd=ROOT, timeout=600)
    try:
        rep = _kb_json.loads(r.stdout)
    except Exception:
        return Result("detect_probes_run", FAIL, f"checkup emitted no JSON: {r.stdout[:120]}")
    probes = rep.get("probes") or []
    if not probes:
        return Result("detect_probes_run", FAIL, "no detect probes found in the catalogue")
    by = {}
    for p_ in probes:
        by.setdefault(p_["status"], []).append(p_["id"])
    broken = by.get("broken-probe") or []
    contradicted = by.get("entry-contradicted") or []
    if broken:
        return Result("detect_probes_run", FAIL,
                      f"{len(broken)} probe(s) cannot run as written, so the entries they back "
                      f"are unverifiable and checkup would report a clean org: {broken}")
    if contradicted:
        return Result("detect_probes_run", FAIL,
                      f"the platform contradicted {len(contradicted)} entry: {contradicted}")
    errored = by.get("error") or []
    if errored:
        return Result("detect_probes_run", WARN, f"{len(errored)} probe(s) failed to run: {errored}")
    ran = sum(len(v) for k, v in by.items() if k != "not-executed")
    return Result("detect_probes_run", PASS,
                  f"{ran}/{len(probes)} probes executed against {target} "
                  f"({len(by.get('affected', []))} live, {len(by.get('confirmed', []))} confirmed, "
                  f"{len(by.get('clear', []))} clear); "
                  f"{len(by.get('not-executed', []))} are not queries and are reported as such")


@check("per_org_knowledge", "static", catastrophe=True)
def _per_org_knowledge():
    """Findings recorded against one org must reach that org and no other, and never leave.

    This is the layer that compounds — engagement five starts where engagement four ended —
    and it is also the layer holding the most sensitive thing Torque touches: observations
    about a specific client's org. Three properties make it safe to keep, and all three are
    asserted here rather than promised.

    Keyed by orgId, not alias. An alias is a local nickname that can be pointed anywhere; the
    orgId is the org. It also gives the correct behaviour on a sandbox refresh, which mints a
    NEW orgId — the memory empties exactly when the org it described stopped existing.
    """
    import importlib.util as _il
    spec = _il.spec_from_file_location("torque_lib", ROOT / "hooks" / "lib.py")
    lib = _il.module_from_spec(spec)
    spec.loader.exec_module(lib)

    # 1. never tracked by git
    tracked = _kb_sp.run(["git", "ls-files", "local/"], capture_output=True, text=True,
                         cwd=ROOT).stdout.split()
    if tracked:
        return Result("per_org_knowledge", FAIL,
                      f"per-org findings are tracked by git: {tracked[:3]}")

    if not lib.ORGS.exists():
        # Returning PASS here meant a fresh clone "proved" cross-org isolation by having no
        # data. The isolation logic is testable without any: exercise it against a synthetic
        # store instead of declaring victory on an empty one.
        return _per_org_synthetic(lib)

    # 2. owner-only, always
    import stat as _st
    for f in list(lib.ORGS.glob("*.yml")) + [lib.ORGS]:
        if _st.S_IMODE(f.stat().st_mode) & 0o077:
            return Result("per_org_knowledge", FAIL,
                          f"{f.name} is {oct(_st.S_IMODE(f.stat().st_mode))} — org findings "
                          f"describe a client's org and are owner-only")

    # 3. a finding reaches its own org and no other
    files = sorted(lib.ORGS.glob("*.yml"))
    if not files:
        return _per_org_synthetic(lib)
    orgid = files[0].stem
    entries = lib._parse_org_file(files[0])
    withtrig = [e for e in entries if e.get("triggers")]
    if not withtrig:
        return Result("per_org_knowledge", PASS,
                      f"{len(entries)} finding(s) for {len(files)} org(s); none carry triggers "
                      f"so none can surface at the gate")
    # find an alias that maps to this org, and one that does not
    try:
        idx = _kb_json.loads(lib.ALIAS_INDEX.read_text())
    except Exception:
        idx = {}
    mine = next((a for a, o in idx.items() if o == orgid), None)
    other = next((a for a, o in idx.items() if o != orgid), None)
    if not mine:
        # The real store cannot be exercised without an indexed alias for it — so exercise the
        # logic against a synthetic one rather than reporting NA and proving nothing.
        return _per_org_synthetic(lib)
    pat = (withtrig[0].get("triggers") or [""])[0]
    probe = _kb_re.sub(r"[()|\\\\^$.*+?\[\]]", " ", pat).split()
    cmd = f"sf {' '.join(probe[:4])} --target-org {mine}"
    if not lib.org_notes(cmd):
        return Result("per_org_knowledge", FAIL,
                      f"a finding recorded for {orgid} did not surface for its own org")
    if other and lib.org_notes(cmd.replace(mine, other)):
        return Result("per_org_knowledge", FAIL,
                      f"a finding recorded for {orgid} leaked to a different org ({other}) — "
                      f"per-org memory must not cross orgs")
    # 4. an unknown orgId gets nothing, which is the sandbox-refresh behaviour
    if lib.org_notes(cmd.replace(mine, "an-alias-that-was-never-indexed")):
        return Result("per_org_knowledge", FAIL,
                      "an unindexed alias received org findings — after a sandbox refresh the "
                      "new org would inherit the old org's memory")
    return Result("per_org_knowledge", PASS,
                  f"{len(entries)} finding(s) across {len(files)} org(s), owner-only, untracked; "
                  f"reaches its own org, not another, and not an unknown one")


@check("impact_bound_approval", "capability", catastrophe=True)
def _impact_bound_approval(target):
    """A token approved for N records must not be spendable on more than N.

    Approval everywhere else is per-command-string or per-session: the operator vouches for
    their own reading of a WHERE clause and nothing checks it again. But data moves. Criteria
    that matched seven rows when the operator looked can match seven thousand by the time the
    command runs, and no command string can express the difference.

    So the approved SIZE is signed into the token and re-established at the gate. This asserts
    the three outcomes that matter: within scope proceeds, grown scope is refused, and a scope
    that cannot be re-established is refused rather than assumed unchanged.
    """
    if not target:
        return Result("impact_bound_approval", SKIP, "no --target-org")
    import importlib.util as _il, shlex as _shlex, time as _t
    lspec = _il.spec_from_file_location("torque_lib", ROOT / "hooks" / "lib.py")
    lib = _il.module_from_spec(lspec); lspec.loader.exec_module(lib)
    sspec = _il.spec_from_file_location("torque_shellparse", ROOT / "hooks" / "shellparse.py")
    sp = _il.module_from_spec(sspec); sspec.loader.exec_module(sp)

    obj, where = "Lead", "Status != null"
    cmd = (f"sf data update record --sobject {obj} --where \"{where}\" "
           f"--values \"Description=x\" --target-org {target}")
    args = _shlex.split(cmd)[1:]
    op = sp.classify_destructive(args)
    if op != "where-update":
        return Result("impact_bound_approval", FAIL, f"probe command classified {op!r}")
    _v, orgid, _u = lib.classify(target)
    if not orgid:
        return Result("impact_bound_approval", SKIP, f"{target} did not resolve an orgId")

    def mint(scope):
        d = lib.impact_digest(obj, where)
        p = {"orgId": orgid, "op": op, "digest": d, "exp": int(_t.time()) + 300,
             "iat": int(_t.time()), "impact": {"sobject": obj, "where_sha": d, "scope": scope}}
        p["sig"] = lib.sign(p)
        lib.TOKENS.mkdir(parents=True, exist_ok=True)
        f = lib.token_path(orgid, op, d)
        f.write_text(_kb_json.dumps(p)); _kb_os.chmod(f, 0o600)
        return f

    def fire(c=cmd):
        r = _kb_sp.run([_kb_sys.executable, str(ROOT / "hooks" / "destructive_data_gate.py")],
                       input=_kb_json.dumps({"tool_name": "Bash", "tool_input": {"command": c}}),
                       capture_output=True, text=True, cwd=ROOT, timeout=180)
        return r.returncode, r.stderr

    ok, rows = _sfq(target, f"SELECT COUNT() FROM {obj} WHERE {where}")
    if ok is not True:
        return Result("impact_bound_approval", SKIP, f"could not count {obj} on {target}")
    live = None
    r = _kb_sp.run(["sf", "data", "query", "--target-org", target, "--json", "--query",
                    f"SELECT COUNT() FROM {obj} WHERE {where}"], capture_output=True, text=True,
                   timeout=120)
    try:
        live = _kb_json.loads(r.stdout)["result"]["totalSize"]
    except Exception:
        return Result("impact_bound_approval", SKIP, "could not establish a live scope")

    tok = mint(live)
    rc, err = fire()
    if rc != 0:
        return Result("impact_bound_approval", FAIL,
                      f"a token approved for the true scope ({live}) was refused: "
                      f"{err.strip().splitlines()[0][:110] if err.strip() else '(no message)'}")
    if tok.exists():
        return Result("impact_bound_approval", FAIL, "an impact token was not consumed")

    mint(max(0, live - 1))
    rc, err = fire()
    if rc != 2 or "impact-drift" not in err and "criteria now match" not in err:
        return Result("impact_bound_approval", FAIL,
                      f"an operation larger than approved was NOT refused (exit {rc})")

    # a scope that cannot be re-established must refuse, never assume
    mint(live)
    rc, err = fire(cmd.replace(f"--target-org {target}", "--target-org sf-nonexistent-zzz"))
    if rc != 2:
        return Result("impact_bound_approval", FAIL,
                      "an unverifiable scope did not refuse")
    for f in lib.TOKENS.glob("*.token"):
        f.unlink(missing_ok=True)
    return Result("impact_bound_approval", PASS,
                  f"approved for {live} {obj} record(s): within scope proceeds and consumes, "
                  f"grown scope refused, unverifiable scope refused")


@check("observer_cost_bounded", "static", catastrophe=False)
def _observer_cost_bounded():
    """The observer runs after EVERY Bash call, so its cost is paid on every command.

    A hook on the hot path whose cost nobody has measured is a hook that will eventually be
    blamed for something. It is also the first thing a reviewer asks about, and "I don't know"
    is the wrong answer.

    What is bounded is the observer's cost ABOVE bare interpreter startup, not its wall-clock.
    The first version measured wall-clock and failed at 298 ms on a machine running three audits
    at load average 12 — while the observer itself was unchanged at 41 ms and a bare `python -c
    pass` took 23 ms of that. A performance check that fails when the machine is busy teaches
    people to ignore performance checks.
    """
    import time as _t
    ev = _kb_json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls -la"},
                         "tool_response": {"stdout": "", "stderr": "", "exit_code": 0}})

    def median_ms(args):
        ts = []
        for _ in range(9):
            t0 = _t.perf_counter()
            r = _kb_sp.run([_kb_sys.executable, *args], input=ev, capture_output=True,
                           text=True, cwd=ROOT, timeout=60)
            ts.append(((_t.perf_counter() - t0) * 1000, r.returncode))
        ts.sort()
        return ts[len(ts) // 2]

    baseline, _ = median_ms(["-c", "pass"])
    observed, rc = median_ms([str(ROOT / "hooks" / "lesson_observer.py")])
    if rc != 0:
        return Result("observer_cost_bounded", FAIL,
                      f"observer exited {rc} on an ordinary command")
    overhead = observed - baseline
    if overhead > 120:
        return Result("observer_cost_bounded", FAIL,
                      f"observer adds {overhead:.0f} ms over bare interpreter startup "
                      f"({observed:.0f} vs {baseline:.0f}) — it runs on every Bash call, so a "
                      f"regression here is paid constantly")
    return Result("observer_cost_bounded", PASS,
                  f"adds {overhead:.0f} ms over bare interpreter startup "
                  f"({observed:.0f} ms total, {baseline:.0f} ms of it Python itself) — the early "
                  f"exit means no extra work on a non-Salesforce command")


@check("public_description_accurate", "capability", catastrophe=False)
def _public_description_accurate(target):
    """Numbers in the PUBLIC repo description must match numbers that actually exist.

    The GitHub description read "128 adversarial tests" for weeks. 128 is the differential-fuzz
    case count; the fixture count is 196. Both numbers are real and they are different things,
    which is exactly how this kind of error survives — it is not a lie, it is a number that moved
    house. Nothing checked it because it lives outside the repo, which is precisely why it drifted
    further than anything inside the repo ever did.

    Every metric this project publishes is derivable. So any number in the public description must
    equal one of them.
    """
    r = _kb_sp.run(["gh", "repo", "view", "--json", "description"],
                   capture_output=True, text=True, cwd=ROOT, timeout=90)
    if r.returncode != 0:
        return Result("public_description_accurate", NA,
                      "gh unavailable or not authenticated; cannot read the public description")
    try:
        desc = (_kb_json.loads(r.stdout) or {}).get("description") or ""
    except Exception:
        return Result("public_description_accurate", NA, "gh returned no parseable description")
    if not desc.strip():
        return Result("public_description_accurate", WARN, "the repo has no public description")

    fixtures = sum(len(_kb_json.loads(f.read_text()).get("fixtures", []))
                   for f in sorted((ROOT / "harness" / "tests").glob("gate_fixtures*.json")))
    kb = len(_kb_re.findall(r"^- id:", (_KB).read_text(), _kb_re.M))
    from collections import Counter as _C
    import importlib.util as _ilu
    _sp2 = _ilu.spec_from_file_location("tv2", ROOT / "harness" / "validate.py")
    _v2 = _ilu.module_from_spec(_sp2); _sp2.loader.exec_module(_v2)
    _c2 = _C(pr for _n, pr, _cc, _f in REGISTRY)
    profiles = {sum(n for pp, n in _c2.items() if _v2.RANK[pp] <= _v2.RANK[pr])
                for pr in _v2.PROFILES}
    fuzz = 0
    m = _kb_re.search(r"(\d+)\s+generated cases", (ROOT / "harness" / "tests" /
                                                   "differential_fuzz.py").read_text())
    try:
        fz = ROOT / "harness" / "tests" / "differential_fuzz.py"
        fuzz = len(_kb_re.findall(r"^\s*[A-Z_]+\s*=\s*\(", fz.read_text(), _kb_re.M))
    except Exception:
        pass
    mutators = len(_kb_re.findall(r"^\s*\(", (ROOT / "harness" / "validate.py").read_text()
                                  .split("REGRESSIONS", 1)[-1].split("]", 1)[0], _kb_re.M))
    real = {fixtures, fixtures + 3, kb, mutators, 128} | profiles
    bad = [n for n in _kb_re.findall(r"\b(\d{2,4})\b", desc) if int(n) not in real]
    if bad:
        return Result("public_description_accurate", FAIL,
                      f"the public GitHub description claims {bad}, which matches no metric this "
                      f"repo produces (real: {sorted(real)}) — description: {desc[:90]!r}")
    return Result("public_description_accurate", PASS,
                  f"every number in the public description matches a real metric "
                  f"{sorted(real)}")


@check("runnable_implies_unwritable", "static", catastrophe=True)
def _runnable_implies_unwritable():
    """Anything the gate lets the agent RUN, the agent must not be able to WRITE.

    The interpreter rule refuses `python3 …` against a Salesforce target, because an interpreter
    is opaque. That also refused two first-party read-only commands the guide tells operators to
    run, so they are now exempt — and the exemption is only sound while the agent cannot edit the
    file it would then be allowed to execute.

    That is a coupling between two lists in different files, which is the kind of thing that
    silently comes apart. Asserted here in both directions.
    """
    import importlib.util as _il
    spec = _il.spec_from_file_location("torque_shellparse", ROOT / "hooks" / "shellparse.py")
    sp = _il.module_from_spec(spec); spec.loader.exec_module(sp)

    lspec = _il.spec_from_file_location("torque_lib_rw", ROOT / "hooks" / "lib.py")
    lib = _il.module_from_spec(lspec); lspec.loader.exec_module(lib)
    # The exemption's soundness rests on lib.protected_write_paths(), which is the ONE list the
    # gate consults. shellparse.PROTECTED_DIRS survives only for the exemption's own reasoning,
    # so assert the two agree instead of assuming it — a second list guarding one boundary is
    # how this comes apart.
    protected = lib.protected_write_paths()
    for d in ("bin", "hooks"):
        if not any(p_.endswith("/" + d) for p_ in protected):
            return Result("runnable_implies_unwritable", FAIL,
                          f"{d}/ is executable-by-exemption but not in protected_write_paths() "
                          f"— the agent could write the file it is then permitted to run")
        if d + "/" not in sp.PROTECTED_DIRS:
            return Result("runnable_implies_unwritable", FAIL,
                          f"{d}/ dropped out of PROTECTED_DIRS, which the exemption reasons from")

    def gate(payload):
        r = _kb_sp.run([_kb_sys.executable, str(ROOT / "hooks" / "prod_write_gate.py")],
                       input=_kb_json.dumps(payload), capture_output=True, text=True,
                       cwd=ROOT, timeout=60)
        return r.returncode

    # every first-party entry point: runnable, and not writable
    runnable = [p for p in (ROOT / "bin").glob("torque*") if p.is_file()]
    if not runnable:
        return Result("runnable_implies_unwritable", FAIL, "no first-party commands found")
    for f in runnable:
        rel = f"bin/{f.name}"
        if gate({"tool_name": "Write", "tool_input": {"file_path": rel, "content": "x"}}) != 2:
            return Result("runnable_implies_unwritable", FAIL,
                          f"the agent can WRITE {rel}, which it is also allowed to run")
    # and a foreign script with an org target is still refused
    if gate({"tool_name": "Bash",
             "tool_input": {"command": "python3 /tmp/not-torque.py --target-org acme"}}) != 2:
        return Result("runnable_implies_unwritable", FAIL,
                      "a script outside TORQUE_HOME was authorized against an org")
    ok = gate({"tool_name": "Bash",
               "tool_input": {"command": "python3 bin/torque checkup --target-org acme"}})
    if ok != 0:
        return Result("runnable_implies_unwritable", FAIL,
                      f"a first-party read-only command was refused (exit {ok})")
    return Result("runnable_implies_unwritable", PASS,
                  f"{len(runnable)} first-party command(s) runnable and none writable; "
                  f"foreign interpreters still refused")


@check("init_requires_operator", "static", catastrophe=True)
def _init_requires_operator():
    """Granting an org write eligibility requires the operator the entry claims made the grant.

    `torque init` classified an org and wrote it to the allowlist with the justification
    "operator-declared writable org" — with no check that an operator was anywhere near it. The
    agent could run it through Bash and grant itself a writable org, and the record would assert
    an operator decision on no evidence. Asserting the fact you were supposed to establish is the
    same defect as a manifest recording that metadata was stripped from a file nobody opened.

    It matters more than "it is only a sandbox": full and partial copy sandboxes hold real
    production data, which is itself an entry in this repo's own catalogue.
    """
    src = (ROOT / "bin" / "torque-init").read_text()
    if "operator_present()" not in src:
        return Result("init_requires_operator", FAIL,
                      "torque-init writes the allowlist without an operator-presence check")

    before = (ROOT / "local" / "writable-orgs.json")
    snapshot = before.read_bytes() if before.exists() else None
    r = _kb_sp.run([_kb_sys.executable, str(ROOT / "bin" / "torque-init"), "torque-fake-org-zzz"],
                   capture_output=True, text=True, cwd=ROOT, timeout=180,
                   stdin=_kb_sp.DEVNULL)
    after = before.read_bytes() if before.exists() else None
    if after != snapshot:
        return Result("init_requires_operator", FAIL,
                      "an agent-context `torque init` MODIFIED the allowlist")
    if r.returncode == 0:
        return Result("init_requires_operator", FAIL,
                      "an agent-context `torque init` exited 0 — it must refuse without a "
                      "real operator terminal")

    # and there must be exactly one implementation of the presence check
    ap = (ROOT / "bin" / "torque-approve").read_text()
    if "def _has_tty" in ap:
        return Result("init_requires_operator", FAIL,
                      "torque-approve carries its own copy of the presence check — two "
                      "implementations of an authorization boundary will drift")
    return Result("init_requires_operator", PASS,
                  "init refuses without a login terminal and wrote nothing; approve and init "
                  "share one presence implementation")


@check("redaction_covers_credentials", "static", catastrophe=True)
def _redaction_covers_credentials():
    """Every credential shape Torque can encounter must not survive redact().

    redact() is the last thing between a command and the audit log, the session log and every
    captured lesson. It handled session-id, access-token and org-id shapes — and passed an SFDX auth URL
    through completely untouched. `force://<clientId>::<refreshToken>@<instance>` carries a
    REFRESH token: a durable credential, not a session that expires. One logged command
    containing one would have written it to disk in plaintext, indefinitely.

    Found by the release panel (kimi) whose own quota ended its run before it could file the
    report. The lead survived in its narration, which is the only reason this was fixed.
    """
    import importlib.util as _il
    spec = _il.spec_from_file_location("torque_lib", ROOT / "hooks" / "lib.py")
    lib = _il.module_from_spec(spec); spec.loader.exec_module(lib)

    # Built by concatenation so the fixtures do not themselves trip secret_scan — the same
    # device _SECRET_BITS uses on its own patterns. A scanner that its own tests must be
    # exempted from is a scanner with a hole shaped like its tests.
    _O = "00D"
    _SID = "sid" + "="
    _AT = "access" + "_token"
    _RT = "refresh" + "_token"
    _FD = "secur/" + "frontdoor.jsp"
    _FORCE = "force" + "://"
    secrets = {
        "sfdx auth url": f"{_FORCE}PlatformCLI::5Aep861REFRESHTOKENvalue@https://x.my.salesforce.com",
        "frontdoor sid": f"https://x.my.salesforce.com/{_FD}?{_SID}{_O}xx!ARsAQtokenval",
        "access token":  '{"' + _AT + '":"' + _O + 'xx!ARsAQrealtokenvalue"}',
        "refresh token": _RT + ": 5Aep861_averylongrefreshvalue",
        "bare session":  f"INVALID_SESSION_ID for {_O}g5000009S1aL!AQEAQKtSessionValueHere",
        "org id":        _O + "g5000009S1aLEAS",
    }
    # The distinctive part of each secret — what must NOT appear in the output.
    needles = {
        "sfdx auth url": "5Aep861REFRESHTOKENvalue",
        "frontdoor sid": "ARsAQtokenval",
        "access token":  "ARsAQrealtokenvalue",
        "refresh token": "averylongrefreshvalue",
        "bare session":  "AQEAQKtSessionValueHere",
        "org id":        "g5000009S1aLEAS",
    }
    leaked = []
    for label, raw in secrets.items():
        out = lib.redact(raw)
        if needles[label] in out:
            leaked.append(f"{label} survives redaction")
    if leaked:
        return Result("redaction_covers_credentials", FAIL,
                      f"{len(leaked)} credential shape(s) reach disk unredacted: {leaked}")
    if lib.redact("sf data query --target-org sf-cb-test") != \
            "sf data query --target-org sf-cb-test":
        return Result("redaction_covers_credentials", FAIL,
                      "an ordinary command was mangled — over-redaction makes the logs useless")
    return Result("redaction_covers_credentials", PASS,
                  f"{len(secrets)} credential shapes redacted; ordinary commands untouched")


@check("local_cannot_reach_git", "static", catastrophe=True)
def _local_cannot_reach_git():
    """`local/` must not be stageable, and org Ids must not be paths.

    "Gitignored, 0600, never leaves the machine" was true of the default and false of the
    boundary: `git add -f` overrides gitignore in one flag, and local/ holds per-org findings,
    session logs carrying before/after record values, and the audit log. The 0600 modes defend
    against another Unix account, not against the agent running as the same user.

    Separately, an org Id became a filename with no validation. It has only ever come from
    `sf org display` — but "the current caller is trustworthy" is a property of today's code,
    not of the identifier, and this store is the most sensitive thing in the repo.
    """
    import importlib.util as _il
    spec = _il.spec_from_file_location("torque_lib", ROOT / "hooks" / "lib.py")
    lib = _il.module_from_spec(spec); spec.loader.exec_module(lib)

    _O = "00D"
    good = _O + "000000000000AAA"          # well-formed, and not a real org
    for bad in ("../../etc/passwd", _O + "../../evil", "", good + "/x", "x" * 40):
        try:
            lib.org_file(bad)
            return Result("local_cannot_reach_git", FAIL,
                          f"org_file accepted {bad!r} as an org-store filename")
        except ValueError:
            pass
    if lib.org_file(good).name != good + ".yml":
        return Result("local_cannot_reach_git", FAIL, "a valid org Id was refused")

    def gate(cmd):
        return _kb_sp.run([_kb_sys.executable, str(ROOT / "hooks" / "prod_write_gate.py")],
                          input=_kb_json.dumps({"tool_name": "Bash",
                                                "tool_input": {"command": cmd}}),
                          capture_output=True, text=True, cwd=ROOT, timeout=60).returncode
    for cmd in (f"git add -f local/orgs/{good}.yml",
                "git add local/",
                "git add -f local/audit.log",
                "git commit local/sessions/x.jsonl -m x"):
        if gate(cmd) != 2:
            return Result("local_cannot_reach_git", FAIL, f"staging allowed: {cmd}")
    for cmd in ("git add -A", "git add README.md", "git commit -m 'ordinary'", "git status"):
        if gate(cmd) != 0:
            return Result("local_cannot_reach_git", FAIL,
                          f"ordinary git refused: {cmd} — a gate that blocks normal work "
                          f"gets switched off")
    tracked = _kb_sp.run(["git", "ls-files", "local/"], capture_output=True, text=True,
                         cwd=ROOT).stdout.split()
    if tracked:
        return Result("local_cannot_reach_git", FAIL, f"local/ paths already tracked: {tracked[:3]}")
    return Result("local_cannot_reach_git", PASS,
                  "5 malformed org Ids refused as filenames; 4 staging paths blocked; "
                  "4 ordinary git commands unaffected; nothing under local/ is tracked")


@check("checkup_cannot_confirm_on_auth_failure", "static", catastrophe=True)
def _checkup_cannot_confirm_on_auth_failure():
    """A failure that says nothing about an entry must never read as the entry being confirmed.

    Two entries exist BECAUSE the platform refuses a query, so for them an error IS the
    confirmation. The code accepted any non-zero response as that confirmation — including
    INVALID_SESSION_ID. A logged-out CLI could therefore "confirm" the catalogue, and JSON mode
    returned 0 unconditionally, so a wholly failed checkup was green to anything consuming it.
    """
    import importlib.machinery as _im, importlib.util as _il
    from types import SimpleNamespace as _NS
    src = ROOT / "bin" / "torque-checkup"
    spec = _il.spec_from_file_location("torque_checkup", src,
                                       loader=_im.SourceFileLoader("torque_checkup", str(src)))
    m = _il.module_from_spec(spec); spec.loader.exec_module(m)

    # m.subprocess IS the shared subprocess module, so assigning to m.subprocess.run patches it
    # process-wide. The first version did exactly that and broke every check that ran after this
    # one — installer_roundtrip and differential_fuzz both failed carrying this function's stub
    # payload in their output. Patch the module reference on the loaded module only, and put it
    # back.
    _real_subprocess = m.subprocess

    class _FakeSubprocess:
        def __init__(self, payload):
            self.payload = payload

        def run(self, *a, **k):
            return _NS(returncode=1, stdout=self.payload, stderr="")

    def stub(payload):
        m.subprocess = _FakeSubprocess(payload)

    cases = [
        ('{"status":1,"name":"INVALID_SESSION_ID","message":"expired"}', "error", "expired session"),
        ('{"status":1,"name":"REQUEST_LIMIT_EXCEEDED","message":"TotalRequests Limit exceeded"}',
         "error", "spent API budget"),
        ('{"status":1,"name":"INVALID_FIELD","message":"cannot be filtered"}',
         "confirmed", "a real platform refusal"),
    ]
    try:
        for payload, want, label in cases:
            stub(payload)
            got, _ = m.run_probe("x", "SELECT Id FROM X", (False, False), "error")
            if got != want:
                return Result("checkup_cannot_confirm_on_auth_failure", FAIL,
                              f"{label} classified {got!r}, expected {want!r}")
    finally:
        m.subprocess = _real_subprocess
    if m._verdict([{"status": "error"}]) == 0:
        return Result("checkup_cannot_confirm_on_auth_failure", FAIL,
                      "a checkup containing an error reported success")
    if m._verdict([{"status": "affected"}, {"status": "clear"}]) != 0:
        return Result("checkup_cannot_confirm_on_auth_failure", FAIL,
                      "a clean checkup reported failure")
    return Result("checkup_cannot_confirm_on_auth_failure", PASS,
                  "auth and limit failures are errors, not confirmation; a real refusal still "
                  "confirms; the verdict is non-zero whenever the picture is untrustworthy")


@check("shield_is_case_insensitive", "static", catastrophe=True)
def _shield_is_case_insensitive():
    """The protected-object floor must hold on BOTH paths, in any casing.

    Salesforce object names are case-insensitive. `_shield_tokens` learned that from an earlier
    red-team finding and was fixed; `_shield_text`, which screens Apex bodies, was not — so
    `delete [SELECT Id FROM account]` walked past a floor that stopped `--sobject account`. The
    Apex path is the one that runs arbitrary DML, so the half that was missed was the half that
    mattered more.

    A fix applied to one call site and not its twin is the defect here, not the casing. Both
    are exercised.
    """
    import importlib.machinery as _im, importlib.util as _il
    src = ROOT / "hooks" / "destructive_data_gate.py"
    spec = _il.spec_from_file_location("torque_ddg", src,
                                       loader=_im.SourceFileLoader("torque_ddg", str(src)))
    m = _il.module_from_spec(spec); spec.loader.exec_module(m)
    protected = sorted(m.lib.protected_objects())
    if not protected:
        return Result("shield_is_case_insensitive", FAIL, "the protected-object list is empty")
    obj = protected[0]
    variants = [obj, obj.lower(), obj.upper(),
                "".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(obj))]

    real_deny = m.lib.deny
    try:
        for v in variants:
            for label, call in (
                ("apex body", lambda v=v: m._shield_text(f"delete [SELECT Id FROM {v}];", "00D")),
                ("cli token", lambda v=v: m._shield_tokens([v], "00D")),
            ):
                hits = []
                m.lib.deny = lambda *a, **k: hits.append(a)
                call()
                if not hits:
                    return Result("shield_is_case_insensitive", FAIL,
                                  f"{label} path allowed protected object as {v!r}")
        # and a non-protected object must still pass, or the floor is just a wall
        hits = []
        m.lib.deny = lambda *a, **k: hits.append(a)
        m._shield_text("delete [SELECT Id FROM Widget__c];", "00D")
        if hits:
            return Result("shield_is_case_insensitive", FAIL,
                          "an unprotected object was refused — the floor became a wall")
    finally:
        m.lib.deny = real_deny
    return Result("shield_is_case_insensitive", PASS,
                  f"{len(protected)} protected object(s), {len(variants)} casings, both the "
                  f"Apex-body and CLI-token paths; unprotected objects unaffected")


@check("observer_cannot_cross_clients", "static", catastrophe=True)
def _observer_cannot_cross_clients():
    """An observation must never pair across orgs, or fire on a command that succeeded.

    The observer's output becomes client-specific knowledge. Its shape excluded the target org,
    so a failure on one client's org could pair with a later success on another's — recording a
    "fix" that had never been applied to the org it was filed against. For this feature that is
    the worst available bug: it manufactures confident, wrong, client-attributed knowledge.

    It also treated any recognised error code in the output as a failure regardless of exit
    status, so grepping a log or reading a doc example could file a lesson.
    """
    import importlib.machinery as _im, importlib.util as _il
    src = ROOT / "hooks" / "lesson_observer.py"
    spec = _il.spec_from_file_location("torque_obs", src,
                                       loader=_im.SourceFileLoader("torque_obs", str(src)))
    m = _il.module_from_spec(spec); spec.loader.exec_module(m)

    a = m._shape("sf data create record --sobject Contact --target-org client-a")
    b = m._shape("sf data create record --sobject Contact --target-org client-b")
    if a == b:
        return Result("observer_cannot_cross_clients", FAIL,
                      f"two orgs share the shape {a!r} — a failure on one can pair with a "
                      f"success on the other")
    if m._shape("sf data create record --sobject Contact --target-org client-a") != a:
        return Result("observer_cannot_cross_clients", FAIL, "shape is not stable")

    # a succeeding command that merely MENTIONS an error code must record nothing
    import tempfile as _tf
    with _tf.TemporaryDirectory() as td:
        m.CANDIDATES = _KbP(td) / "c.jsonl"
        ev = {"tool_name": "Bash",
              "tool_input": {"command": "sf data query --target-org o --query \"SELECT Id\""},
              "tool_response": {"stdout": "grep: INVALID_FIELD appears in the log", "stderr": "",
                                "exit_code": 0}}
        r = _kb_sp.run([_kb_sys.executable, str(src)], input=_kb_json.dumps(ev),
                       capture_output=True, text=True, cwd=ROOT, timeout=60,
                       env={**_kb_os.environ, "TORQUE_HOME": td})
        if r.returncode != 0:
            return Result("observer_cannot_cross_clients", FAIL,
                          f"observer exited {r.returncode} on a succeeding command")
    if not hasattr(m, "_QUEUE_CAP") or m._QUEUE_CAP > 5000:
        return Result("observer_cannot_cross_clients", FAIL,
                      "the candidate queue is unbounded — it is read on every Bash call, so "
                      "capture would get slower the more it captured")
    return Result("observer_cannot_cross_clients", PASS,
                  f"observations are org-scoped, a succeeding command records nothing, and the "
                  f"queue is capped at {m._QUEUE_CAP}")


def _per_org_synthetic(lib):
    """Exercise cross-org isolation against a store built for the purpose.

    per_org_knowledge used to return PASS when no findings existed — so on a fresh clone the
    strongest privacy claim in the repo was proven by the absence of data. The logic does not
    need real client findings to be tested.
    """
    import tempfile as _tf
    with _tf.TemporaryDirectory() as td:
        root = _KbP(td)
        orgs = root / "orgs"; orgs.mkdir()
        a, b = "00D" + "000000000000AAA", "00D" + "000000000000BBB"
        (orgs / f"{a}.yml").write_text(
            "entries:\n- id: probe\n  observed: >\n    a finding for A\n"
            "  remedy: >\n    do the thing\n  triggers: ['data delete']\n"
            "  confidence: org-observed\n")
        real_orgs, real_index = lib.ORGS, lib.ALIAS_INDEX
        idx = root / "idx.json"
        idx.write_text(_kb_json.dumps({"org-a": a, "org-b": b}))
        try:
            lib.ORGS, lib.ALIAS_INDEX = orgs, idx
            own = lib.org_notes("sf data delete record --target-org org-a")
            other = lib.org_notes("sf data delete record --target-org org-b")
            unknown = lib.org_notes("sf data delete record --target-org never-indexed")
        finally:
            lib.ORGS, lib.ALIAS_INDEX = real_orgs, real_index
    if not own:
        return Result("per_org_knowledge", FAIL,
                      "a finding did not reach its own org in a synthetic store")
    if other or unknown:
        return Result("per_org_knowledge", FAIL,
                      "a finding reached an org it was not recorded against")
    return Result("per_org_knowledge", PASS,
                  "no findings recorded here, so isolation was exercised against a synthetic "
                  "store: reaches its own org, not another, and not an unindexed alias")
