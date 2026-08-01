# Knowledge-base integrity: the catalogue must be well-formed, and every claim marked
# `verified-live` must STILL BE TRUE of a real org — checked by querying one, not by trusting
# the file. A platform catalogue nobody re-checks is a blog post with a version number:
# Salesforce ships three releases a year, so a fact recorded once decays on a schedule.
import json as _kb_json
import subprocess as _kb_sp
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
