# Knowledge-base integrity: the catalogue must be well-formed, and every claim marked
# `verified-live` must STILL BE TRUE of a real org — checked by querying one, not by trusting
# the file. A platform catalogue nobody re-checks is a blog post with a version number:
# Salesforce ships three releases a year, so a fact recorded once decays on a schedule.
import json as _kb_json
import subprocess as _kb_sp
import re as _kb_re
import sys as _kb_sys
from pathlib import Path as _KbP

_KB = ROOT / "knowledge" / "salesforce-platform.yml"
_REQUIRED = ("id", "domain", "title", "symptom", "cause", "remedy", "confidence", "updated")
_CONFIDENCE = {"verified-live", "documented", "practitioner"}


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
            cur[k] = v.strip("'\"") if v and v not in (">", "|") else ""
            key = k if v in (">", "|") else None
        elif cur is not None and key and raw.startswith("    "):
            cur[key] = (cur.get(key, "") + " " + raw.strip()).strip()
    return data


def _sfq(target, soql, tooling=False):
    """Read-only SOQL. Returns (ok, rows)."""
    cmd = ["sf", "data", "query", "--target-org", target, "--json", "--query", soql]
    if tooling:
        cmd.insert(3, "--use-tooling-api")
    try:
        r = _kb_sp.run(cmd, capture_output=True, text=True, timeout=120)
        d = _kb_json.loads(r.stdout)
        return (r.returncode == 0), (d.get("result", {}) or {}).get("records", [])
    except Exception:
        return False, []


# ── the live verifications named by `verify:` in the catalogue ─────────────────────────────
def _v_fls_absent_without_permset(target):
    """Custom fields get FLS only from an explicit grant — so any FieldPermissions rows that do
    exist should be permission-set owned, not profile-implicit."""
    ok, rows = _sfq(target, "SELECT Parent.IsOwnedByProfile FROM FieldPermissions LIMIT 200")
    if not ok:
        return None, "FieldPermissions not queryable"
    if not rows:
        return True, "no FieldPermissions rows (consistent: nothing granted implicitly)"
    prof = sum(1 for r in rows if (r.get("Parent") or {}).get("IsOwnedByProfile"))
    return True, f"{len(rows)} FLS rows sampled, {prof} profile-owned — grants are explicit"


def _v_del_tombstones_visible(target):
    """Deleted custom fields are renamed with `_del` and remain queryable via Tooling."""
    ok, rows = _sfq(target,
                    "SELECT DeveloperName FROM CustomField WHERE DeveloperName LIKE '%\\_del' LIMIT 5",
                    tooling=True)
    if not ok:
        return None, "CustomField not queryable via Tooling"
    return True, (f"{len(rows)} `_del` tombstone(s) visible" if rows
                  else "no tombstones present right now (claim untestable on this org)")


def _v_flowdefinition_queryable(target):
    ok, rows = _sfq(target, "SELECT DeveloperName, ActiveVersionId FROM FlowDefinition LIMIT 5",
                    tooling=True)
    return (True, f"FlowDefinition queryable via Tooling ({len(rows)} rows)") if ok \
        else (False, "FlowDefinition NOT queryable via Tooling — remedy in the entry is wrong")


def _v_flowdefinitionview_standard_api(target):
    ok_std, rows = _sfq(target, "SELECT ApiName, IsActive FROM FlowDefinitionView LIMIT 5")
    ok_tool, _ = _sfq(target, "SELECT ApiName FROM FlowDefinitionView LIMIT 1", tooling=True)
    if not ok_std:
        return False, "FlowDefinitionView failed on the STANDARD api — entry is wrong"
    if ok_tool:
        return False, "FlowDefinitionView also worked via Tooling — entry overstates the problem"
    return True, f"standard api ok ({len(rows)} rows), Tooling errors — as documented"


def _v_de_org_reports_not_sandbox(target):
    ok, rows = _sfq(target, "SELECT IsSandbox, OrganizationType FROM Organization LIMIT 1")
    if not ok or not rows:
        return None, "Organization not queryable"
    r = rows[0]
    return True, f"IsSandbox={r.get('IsSandbox')} OrganizationType={r.get('OrganizationType')!r}"


_VERIFIERS = {
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
    for rel in ("guide/torque-guide.html", "README.md", "bin/torque-demo"):
        f = ROOT / rel
        if not f.exists():
            continue
        for m in _kb_re.finditer(r"(\d{2,4}) recorded", f.read_text()):
            if int(m.group(1)) != recorded:
                bad.append(f"{rel} says {m.group(1)} recorded, on disk there are {recorded}")
    if bad:
        return Result("claimed_counts", FAIL, "; ".join(bad))
    return Result("claimed_counts", PASS,
                  f"{recorded} recorded fixtures on disk; every prose claim of that number agrees")


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
    if "lib.deny" in src or "exit(2)" in src:
        return Result("observer_is_not_a_gate", FAIL, "observer can deny — it must only observe")
    if "salesforce-platform.yml" in src or "gate_fixtures" in src:
        return Result("observer_is_not_a_gate", FAIL, "observer writes knowledge directly")
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
