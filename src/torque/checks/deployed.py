"""`torque check deployed`: what a deploy left in the org, read-only.

Retrieves the same components into a fresh private temporary folder and compares them with the submitted
source; reads flow activation through the flow definition (the advisory package's route); reads which
permission sets grant each new field (FieldPermissions) and whether each validation rule is active.
Every Salesforce CLI call names --target-org, goes through Torque's launcher with an argument list, and
only retrieves or queries: nothing in the org changes. `sf org display` is never called.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile

from . import compare, rules, scope as scopes
from .components import find_project, read_tree
from .deploy_result import ResultError, _first_object
from .rules_access import needs_grant

try:  # the shared launcher finds sf where Windows installs it as a batch file
    from jsc_common.tools import run as run_tool
except ImportError:  # pragma: no cover
    run_tool = subprocess.run

LABEL = ("proven against a stubbed CLI and one live run on a Developer Edition org, 2026-11; not yet run against a "
         "sandbox or a production org")
RETRIEVABLE = {"CustomField", "CustomObject", "ValidationRule", "RecordType", "Flow", "PermissionSet",
               "PermissionSetGroup", "CustomPermission", "Profile", "FlexiPage", "Layout", "CustomMetadata",
               "ApexClass", "ApexTrigger"}
NOT_FOUND = re.compile(r"Entity of type '(\w+)' named '([^']+)' cannot be found")
CHUNK = 100
RETRIEVE_TIMEOUT = 900


class InputError(ValueError):
    pass


def _reason(proc) -> str:
    try:
        from jsc_advisory.sf import first_reason
    except ImportError:  # pragma: no cover
        return (proc.stderr or proc.stdout or "no output").strip()[:220]
    return first_reason(proc.stderr or "", proc.stdout or "")


def retrieve(alias: str, members: list[str], folder: str) -> tuple[set, list[str], int]:
    """(not found in the org as (type, name lower), errors, calls that succeeded)."""
    missing, errors, ok = set(), [], 0
    for i in range(0, len(members), CHUNK):
        argv = ["sf", "project", "retrieve", "start", "--target-org", alias, "--target-metadata-dir", folder,
                "--unzip", "--json"]
        for m in members[i:i + CHUNK]:
            argv += ["--metadata", m]
        try:
            proc = run_tool(argv, capture_output=True, text=True, timeout=RETRIEVE_TIMEOUT, cwd=folder)
        except (subprocess.TimeoutExpired, OSError) as exc:
            errors.append(f"sf project retrieve start: {type(exc).__name__}")
            continue
        payload, _ = _first_object(proc.stdout or "")
        result = payload.get("result") if isinstance(payload, dict) else None
        if proc.returncode != 0 or not isinstance(result, dict):
            message = payload.get("message") if isinstance(payload, dict) else None
            errors.append(" ".join(str(message or _reason(proc)).split())[:240])
            continue
        ok += 1
        for msg in result.get("messages") or []:
            m = NOT_FOUND.search(str(msg.get("problem") if isinstance(msg, dict) else msg))
            if m:
                missing.add((m.group(1), m.group(2).lower()))
    return missing, errors, ok


def _client():
    from jsc_advisory.sf import SfClient
    return SfClient()


def flow_state(client, alias: str, comp) -> dict:
    from jsc_advisory.flow import verify_flow
    from jsc_advisory.sf import Unknown, records, soql_literal
    out = {"name": comp.name, "source_status": comp.node.value("status") if comp.node is not None else ""}
    try:
        report = verify_flow(client, alias, comp.name)
    except (Unknown, ValueError) as exc:
        return {**out, "status": "UNKNOWN", "message": f"could not read the flow definition: {exc}"}
    tooling = report["sources"].get("tooling_flow_definition", {})
    out.update(status=report["status"], undetermined=report["undetermined"])
    versions = {}
    try:
        rows = records(client.query(alias, "SELECT DeveloperName, ActiveVersion.VersionNumber, "
                                    "LatestVersion.VersionNumber FROM FlowDefinition WHERE DeveloperName = "
                                    f"'{soql_literal(comp.name)}'", tooling=True))
        if rows:
            versions = {"active": (rows[0].get("ActiveVersion") or {}).get("VersionNumber"),
                        "latest": (rows[0].get("LatestVersion") or {}).get("VersionNumber")}
    except Unknown as exc:
        out.setdefault("undetermined", []).append(f"version numbers: {exc}")
    active_id, latest_id = tooling.get("active_version_id"), tooling.get("latest_version_id")
    out.update(active_version=versions.get("active"), latest_version=versions.get("latest"))
    if report["status"] == "NOT_FOUND":
        out.update(latest_is_active=None, message="the org has no flow with this name")
    elif "error" in tooling or latest_id is None:
        out.update(latest_is_active=None, message="the active and latest versions could not be read")
    elif not active_id:
        out.update(latest_is_active=False, message=f"no version is active; the latest is version "
                                                   f"{versions.get('latest', '?')}")
    elif active_id == latest_id:
        out.update(latest_is_active=True, message=f"the latest version {versions.get('latest', '?')} is the "
                                                  "active one")
    else:
        out.update(latest_is_active=False, message=f"the latest version {versions.get('latest', '?')} is not the "
                                                   f"active one; the org runs version {versions.get('active', '?')}")
    if out.get("latest_is_active") is False and out["source_status"] == "Active":
        out["message"] += " (the file asked for Active)"
    return out


def field_access(client, alias: str, fields: list, as_sets: list[str]) -> list[dict]:
    from jsc_advisory.sf import Unknown, records, soql_literal
    out = []
    for i in range(0, len(fields), 50):
        chunk = fields[i:i + 50]
        names = ", ".join(f"'{soql_literal(c.name)}'" for c in chunk)
        try:
            rows = records(client.query(alias, "SELECT Field, Parent.Name, Parent.IsOwnedByProfile, "
                                        "Parent.Profile.Name, PermissionsRead, PermissionsEdit FROM FieldPermissions "
                                        f"WHERE Field IN ({names})"))
        except Unknown as exc:
            out += [{"name": c.name, "rows": None, "message": f"could not read FieldPermissions: {exc}"} for c in chunk]
            continue
        for c in chunk:
            mine = [r for r in rows if str(r.get("Field") or "").lower() == c.name.lower()
                    and (r.get("PermissionsRead") or r.get("PermissionsEdit"))]
            sets, profiles = [], []
            for r in mine:
                parent = r.get("Parent") or {}
                grant = "read and edit" if r.get("PermissionsEdit") else "read"
                if parent.get("IsOwnedByProfile"):
                    profiles.append({"profile": (parent.get("Profile") or {}).get("Name") or parent.get("Name"),
                                     "access": grant})
                else:
                    sets.append({"permission_set": parent.get("Name"), "access": grant})
            named = {}
            for n in as_sets:
                hit = next((s for s in sets if str(s["permission_set"]).lower() == n.lower()), None)
                named[n] = hit["access"] if hit else "none"
            if not mine:
                message = "no permission set or profile grants it: only users who see all data can see it"
            elif not sets:
                message = "granted only through profiles"
            else:
                message = "granted by " + ", ".join(f"{s['permission_set']} ({s['access']})" for s in sets)
            if any(v == "none" for v in named.values()):
                message += "; not granted by " + ", ".join(k for k, v in named.items() if v == "none")
            out.append({"name": c.name, "rows": len(mine), "permission_sets": sets, "profiles": profiles,
                        "as_permission_sets": named, "message": message})
    return out


def rule_states(client, alias: str, comps: list) -> list[dict]:
    from jsc_advisory.sf import Unknown, records, soql_literal
    if not comps:
        return []
    names = ", ".join(sorted({f"'{soql_literal(c.name.split('.', 1)[1])}'" for c in comps}))
    try:
        rows = records(client.query(alias, "SELECT ValidationName, Active, EntityDefinition.QualifiedApiName "
                                    f"FROM ValidationRule WHERE ValidationName IN ({names})", tooling=True))
    except Unknown as exc:
        return [{"name": c.name, "active": None, "message": f"could not read: {exc}"} for c in comps]
    out = []
    for c in comps:
        obj, rule = c.name.split(".", 1)
        row = next((r for r in rows if str(r.get("ValidationName") or "").lower() == rule.lower()
                    and str((r.get("EntityDefinition") or {}).get("QualifiedApiName") or "").lower() == obj.lower()),
                   None)
        if row is None:
            out.append({"name": c.name, "active": None, "message": "not found in the org"})
        else:
            active = bool(row.get("Active"))
            out.append({"name": c.name, "active": active, "message": "active" if active else "inactive"})
    return out


def check(target_org: str, *, changed_from: str | None = None, source_dir: str | None = None,
          only: list[str] | None = None, as_permission_sets: list[str] | None = None) -> dict:
    if not target_org or target_org.startswith("-"):
        raise InputError("--target-org needs the org alias or username")
    if not (changed_from or source_dir or only):
        raise InputError("say what was deployed: --changed-from RESULT.json, --source-dir DIR or --only Type:Name")
    paths = [source_dir or "."]
    if not os.path.isdir(paths[0]):
        raise InputError(f"--source-dir {paths[0]} is not a folder")
    try:
        if changed_from:
            sc = scopes.changed_from(paths, changed_from)
        elif only:
            sc = scopes.only(paths, only)
        else:
            sc = scopes.everything_under(paths)
    except (ResultError, scopes.ScopeError) as exc:
        raise InputError(str(exc)) from None
    failed = [c for c in sc.in_scope if c.status == "failed"]
    comps = [c for c in sc.in_scope if c.status != "failed"]
    members = sorted({f"{c.type}:{c.name}" for c in comps if c.type in RETRIEVABLE})
    folder = tempfile.mkdtemp(prefix="torque-deployed-")
    try:
        missing, errors, ok = retrieve(target_org, members, folder) if members else (set(), [], 0)
        org_side = {c.key: c for c in read_tree([folder])}
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    requested = {(c.type, c.name.lower()) for c in comps}
    rows = []
    for c in comps:
        row = {"type": c.type, "name": c.name, "status": c.status, "differences": [], "not_compared_entries": 0,
               "org_only": []}
        if c.type not in RETRIEVABLE:
            row.update(comparison="could-not-compare", reason=f"{c.type} is not compared by this command")
        elif c.key in org_side:
            result, diffs, skipped, org_only = compare.compare(c, org_side[c.key], requested)
            row.update(comparison=result, differences=diffs, not_compared_entries=skipped, org_only=org_only)
        elif c.key in missing:
            row.update(comparison="not-in-org", reason="the org reports it cannot be found")
        elif errors and not ok:
            row.update(comparison="could-not-compare", reason=f"the retrieve failed: {errors[0]}")
        else:
            row.update(comparison="could-not-compare", reason="the retrieve returned no file for it")
        rows.append(row)
    client = _client()
    project = find_project(paths)
    ctx = rules.build_context(project, sc.everything, comps, sc.grant_scope, None)
    flows = [flow_state(client, target_org, c) for c in comps if c.type == "Flow"]
    fields = [c for c in comps if c.type == "CustomField" and c.status in ("new", "unknown") and needs_grant(c, ctx)]
    access = field_access(client, target_org, fields, list(as_permission_sets or []))
    vrules = rule_states(client, target_org, [c for c in comps if c.type == "ValidationRule"])
    attempted = bool(members or flows or access or vrules)
    reached = not attempted or ok > 0 or any(f.get("status") not in (None, "UNKNOWN") for f in flows) or any(
        a.get("rows") is not None for a in access) or any("could not read" not in r["message"] for r in vrules)
    counts = {k: sum(1 for r in rows if r["comparison"] == k)
              for k in ("same", "same-org-has-more", "different", "could-not-compare", "not-in-org")}
    return {
        "schema_version": 1, "command": "torque check deployed", "label": LABEL, "target_org": target_org,
        "scope": {"mode": sc.mode, "source": sc.source, "components": len(comps),
                  "failed_in_result": [f"{c.type}:{c.name}" for c in failed], "not_found_locally": sc.not_found},
        "retrieve": {"members": len(members), "calls_succeeded": ok, "errors": errors,
                     "folder": "a fresh private temporary folder, removed afterwards"},
        "components": rows, "flows": flows, "fields": access, "validation_rules": vrules,
        "totals": {**counts, "flows_latest_not_active": sum(1 for f in flows if f.get("latest_is_active") is False),
                   "fields_without_grants": sum(1 for a in access if a.get("rows") == 0),
                   "inactive_validation_rules": sum(1 for r in vrules if r["active"] is False)},
        "org_reached": reached,
        "not_checked": ["permission set assignments and what a user really sees (test as that user)",
                        "whether the change does what the requirement asks (business acceptance)",
                        "components of types this command does not compare"],
    }


def text(rep: dict) -> str:
    t = rep["totals"]
    lines = [f"torque check deployed: org {rep['target_org']} ({rep['label']}).",
             f"Scope: {rep['scope']['components']} component(s) ({rep['scope']['mode']}"
             + (f" {rep['scope']['source']}" if rep["scope"]["source"] else "") + ").",
             f"Retrieved {rep['retrieve']['members']} into a fresh private folder (removed afterwards): "
             f"{t['same'] + t.get('same-org-has-more', 0)} as submitted ({t.get('same-org-has-more', 0)} of them with "
             f"more in the org), {t['different']} different, {t['could-not-compare']} could not compare, "
             f"{t['not-in-org']} not in the org."]
    for e in rep["retrieve"]["errors"]:
        lines.append(f"Retrieve error: {e}")
    for c in rep["scope"]["failed_in_result"]:
        lines.append(f"Not deployed (failed in the result): {c}")
    for r in rep["components"]:
        tail = "; ".join(r["differences"][:6]) or r.get("reason", "")
        more = f" (+{len(r['differences']) - 6} more)" if len(r["differences"]) > 6 else ""
        skipped = (f" ({r['not_compared_entries']} entries about components not in this retrieve not compared)"
                   if r["not_compared_entries"] else "")
        shown = {"same-org-has-more": "same as submitted"}.get(r["comparison"], r["comparison"])
        only = r.get("org_only") or []
        also = ""
        if only and r["comparison"] != "different":
            also = ("; the org also has " + ", ".join(only[:5]) + (f" (+{len(only) - 5} more)" if len(only) > 5 else "")
                    + " (defaults Salesforce adds on save, or someone else's change)")
        lines.append(f"  {r['type']} {r['name']}: {shown}" + (f": {tail}{more}" if tail else "") + also + skipped)
    if rep["flows"]:
        lines.append("Flow activation:")
        lines += [f"  {f['name']}: {f['message']}" for f in rep["flows"]]
    if rep["fields"]:
        lines.append("Field access in the org (FieldPermissions):")
        lines += [f"  {a['name']}: {a['message']}" for a in rep["fields"]]
    if rep["validation_rules"]:
        lines.append("Validation rules:")
        lines += [f"  {r['name']}: {r['message']}" for r in rep["validation_rules"]]
    lines.append(f"Totals: {t['different']} different, {t['not-in-org']} not in the org, {t['could-not-compare']} not "
                 f"compared, {t['flows_latest_not_active']} flow(s) whose latest version is not active, "
                 f"{t['fields_without_grants']} new field(s) with no grant, {t['inactive_validation_rules']} inactive "
                 f"validation rule(s). Not checked: {'; '.join(rep['not_checked'])}.")
    return "\n".join(lines)


def register(actions) -> None:
    p = actions.add_parser(
        "deployed", help=f"compare what a deploy left in the org with the submitted source ({LABEL})",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "After a deploy: retrieve the same components into a fresh private temporary folder and compare them\n"
            "with the submitted source (same, different with the element that differs, could not compare, not in\n"
            "the org); report whether each flow's latest version is the active one; which permission sets grant\n"
            "each new field in the org (and whether each --as-permission-set does); whether each validation rule\n"
            "is active. Read-only: it retrieves and queries through the Salesforce CLI with --target-org on every\n"
            f"call and changes nothing in the org. Status: {LABEL}.\n\n"
            "Exit: 0 when the report completes, whatever it found; 2 unusable input; 3 the org could not be read."))
    p.add_argument("--target-org", required=True, dest="target_org")
    what = p.add_mutually_exclusive_group()
    what.add_argument("--changed-from", metavar="RESULT.json", help="the saved deploy result; Created and Changed "
                                                                     "components are compared")
    what.add_argument("--only", metavar="Type:Name", action="append", help="compare only this component (repeatable)")
    p.add_argument("--source-dir", metavar="DIR", help="the submitted source (default: the current folder); alone, "
                                                        "everything under it is compared")
    p.add_argument("--as-permission-set", metavar="NAME", action="append", default=[], dest="as_permission_sets",
                   help="also report whether this permission set grants each new field (repeatable)")
    p.add_argument("--json", action="store_true")


def run(parsed, out, err) -> int:
    try:
        rep = check(parsed.target_org, changed_from=parsed.changed_from, source_dir=parsed.source_dir,
                    only=parsed.only, as_permission_sets=parsed.as_permission_sets)
    except InputError as exc:
        err.write(f"torque check deployed: {exc}\n")
        return 2
    out.write((json.dumps(rep, indent=2, ensure_ascii=False) if parsed.json else text(rep)) + "\n")
    if not rep["org_reached"]:
        err.write("torque check deployed: the org could not be read"
                  + (f" ({rep['retrieve']['errors'][0]})" if rep["retrieve"]["errors"] else "")
                  + ". Check that the alias has a CLI login (`sf org list`), then run it again.\n")
        return 3
    return 0
