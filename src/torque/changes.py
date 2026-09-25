"""Private engagement changes: requirements, decisions, observations and handoffs.

Records inform the user's work. They neither authorize operations nor introduce a
required lifecycle. Manual checks stay reported; Metadata API observations retain
their exact job and limited technical scope.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
from uuid import uuid4

from . import workspace as ws

_CHANGE_ID = re.compile(r"chg-[a-f0-9]{12}\Z")
_EVENT_ID = re.compile(r"[0-9]{8}T[0-9]{12}Z-[a-f0-9]{12}\Z")
_RESULTS = ("pass", "fail", "unknown", "not_run")
# Connected-mode approval events, written by Torque's approval code (basis "torque_approval").
# approval_executed (a16, D14): the approved call ran, and how it ended (the post-call hook).
APPROVAL_KINDS = ("approval_request", "approval_grant", "approval_deny", "approval_consume", "approval_executed")
# An independent before-state captured for a production approval (basis "torque_before_state").
BEFORE_STATE_KIND = "before_state"
_TORQUE_BASIS = {**{kind: "torque_approval" for kind in APPROVAL_KINDS}, BEFORE_STATE_KIND: "torque_before_state"}


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ws.WorkspaceError(f"{name} must be nonempty")
    return value.strip()


def _timestamp(value: object) -> bool:
    try:
        return isinstance(value, str) and datetime.fromisoformat(value).utcoffset() is not None
    except ValueError:
        return False


def _org(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not any(c.isspace() for c in value)


def _directory(workspace: str | Path, client: str) -> tuple[Path, dict]:
    root, _, config = ws.load_client(workspace, client)
    return ws._inside(root, root / "changes"), config


def create_change(workspace: str | Path, client: str, title: str, outcome: str,
                  criteria: list[str] | None = None, org: str | None = None) -> dict:
    title, outcome = _text(title, "title"), _text(outcome, "business outcome")
    criteria = [_text(c, "acceptance criterion") for c in (criteria or [])]
    if org is not None and not _org(org):
        raise ws.WorkspaceError("org must be one explicit alias, username or ID")
    directory, config = _directory(workspace, client)
    identifier = "chg-" + uuid4().hex[:12]
    record = {"schema": "torque.change/1", "id": identifier, "client": config["slug"],
              "title": title, "outcome": outcome, "created_at": ws._now(),
              "planned_org": org, "criteria": [{"id": f"AC{i + 1}", "text": text}
                                                for i, text in enumerate(criteria)]}
    existed = directory.exists()
    directory.mkdir(mode=0o700, exist_ok=True)
    pending = ws._inside(directory, directory / (".pending-" + uuid4().hex))
    pending.mkdir(mode=0o700)
    try:
        (pending / "events").mkdir(mode=0o700)
        (pending / "evidence").mkdir(mode=0o700)
        ws._write_json(pending / "change.json", record)
        # V2 I6: the delegated approver reads change records.
        ws.share_with_approver(workspace, *([] if existed else [directory]), pending, pending / "events",
                               pending / "evidence", pending / "change.json")
        pending.rename(directory / identifier)
    finally:
        if pending.exists():
            shutil.rmtree(pending)
    return record


def load_change(workspace: str | Path, client: str, identifier: str) -> tuple[Path, dict]:
    if not isinstance(identifier, str) or not _CHANGE_ID.fullmatch(identifier):
        raise ws.WorkspaceError("change ID must be an ID returned by torque change create/list")
    directory, config = _directory(workspace, client)
    root = ws._inside(directory, directory / identifier)
    record = ws._read_json(ws._inside(directory, root / "change.json"))
    if (record.get("schema") != "torque.change/1" or record.get("id") != identifier
            or record.get("client") != config["slug"] or not isinstance(record.get("title"), str)
            or not isinstance(record.get("outcome"), str) or not isinstance(record.get("criteria"), list)
            or not record["title"].strip() or not record["outcome"].strip()
            or not _timestamp(record.get("created_at"))
            or (record.get("planned_org") is not None and not _org(record["planned_org"]))):
        raise ws.WorkspaceError(f"invalid change record: {identifier}")
    for i, criterion in enumerate(record["criteria"]):
        if (not isinstance(criterion, dict) or criterion.get("id") != f"AC{i + 1}"
                or not isinstance(criterion.get("text"), str) or not criterion["text"].strip()):
            raise ws.WorkspaceError(f"invalid acceptance criteria: {identifier}")
    return root, record


def list_changes(workspace: str | Path, client: str) -> list[dict]:
    directory, _ = _directory(workspace, client)
    if not directory.exists():
        return []
    return sorted((load_change(workspace, client, p.name)[1]
                   for p in directory.iterdir() if _CHANGE_ID.fullmatch(p.name)),
                  key=lambda c: (c["created_at"], c["id"]), reverse=True)


def _capture_file(root: Path, source: str | Path) -> dict:
    raw = Path(source).expanduser().absolute()
    if raw.is_symlink():
        raise ws.WorkspaceError("evidence source must not be a symlink")
    path = raw.resolve()
    # This change's client may use shared firm artifacts, but never another client.
    client = root.parent.parent
    clients = client.parent
    if clients in path.parents and client not in path.parents:
        raise ws.WorkspaceError("evidence belongs to a different client")
    if not path.is_file():
        raise ws.WorkspaceError(f"evidence file does not exist: {path}")
    directory = ws._inside(root, root / "evidence")
    existed = directory.exists()
    directory.mkdir(mode=0o700, exist_ok=True)
    suffix = re.sub(r"[^A-Za-z0-9.]", "", path.suffix)[:16]
    name = uuid4().hex + suffix
    target = ws._inside(root, directory / name)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source_stream, target.open("xb") as destination:
            target.chmod(0o600)
            while chunk := source_stream.read(1024 * 1024):
                digest.update(chunk)
                destination.write(chunk)
    except OSError:
        target.unlink(missing_ok=True)
        raise
    ws.share_with_approver(_workspace_of(root), *([] if existed else [directory]), target)
    return {"path": "evidence/" + name, "name": path.name,
            "sha256": digest.hexdigest(), "bytes": target.stat().st_size,
            "meaning": "Captured bytes; this alone does not verify the claim."}


def _workspace_of(root: Path) -> Path:
    """The workspace holding a change folder (<workspace>/clients/<slug>/changes/<id>)."""
    return root.parent.parent.parent.parent


def _append(root: Path, record: dict, event: dict) -> dict:
    directory = ws._inside(root, root / "events")
    existed = directory.exists()
    directory.mkdir(mode=0o700, exist_ok=True)
    at = datetime.now(timezone.utc)
    identifier = at.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:12]
    value = {"schema": "torque.change-event/1", "id": identifier,
             "change": record["id"], "client": record["client"],
             "created_at": at.isoformat(), **event}
    ws._write_json(ws._inside(root, directory / f"{identifier}.json"), value)
    ws.share_with_approver(_workspace_of(root), *([] if existed else [directory]), directory / f"{identifier}.json")
    return value


# The fields each approval event must carry (a value may be null where it does not
# apply, such as a browser window's command digest).
APPROVAL_EVENT_FIELDS = {
    "approval_request": ("request_id", "command", "command_sha256", "payload_digest", "request_kind", "org_alias",
                         "org_id_18", "org_kind", "validated_job", "before_state_event", "manual_recovery"),
    "approval_grant": ("request_id", "approval_id", "command", "command_sha256", "payload_digest", "org_alias",
                       "org_id_18", "org_kind", "approver", "granted_at", "expires_at", "before_state",
                       "manual_recovery", "validated_job"),
    "approval_deny": ("request_id", "reason", "command", "command_sha256", "payload_digest", "org_alias",
                      "org_id_18", "org_kind", "approver", "before_state_event", "manual_recovery", "validated_job"),
    "approval_consume": ("approval_id", "request_id", "command", "command_sha256", "payload_digest", "org_alias",
                         "org_id_18", "org_kind", "approver", "before_state", "manual_recovery", "validated_job",
                         "granted_at", "expires_at", "session_id", "tool_use_id"),
    "approval_executed": ("approval_id", "tool_use_id", "outcome"),
}


def _missing_fields(kind: str, event: dict) -> list[str]:
    return [key for key in APPROVAL_EVENT_FIELDS.get(kind, ()) if key not in event]


def append_approval_event(workspace: str | Path, client: str, change_id: str, kind: str, fields: dict) -> dict:
    """Record one approval step. These events log what happened; they do not
    authorize anything (the approval store and the gate do)."""
    if kind not in APPROVAL_KINDS:
        raise ws.WorkspaceError(f"unknown approval event kind: {kind}")
    missing = _missing_fields(kind, fields)
    if missing:
        raise ws.WorkspaceError(f"{kind} event is missing: {', '.join(missing)}")
    root, record = load_change(workspace, client, change_id)
    reserved = {"schema", "id", "change", "client", "created_at", "kind", "basis", "summary"}
    extra = {k: v for k, v in fields.items() if k not in reserved}
    summary = fields.get("command") or fields.get("reason") or fields.get("approval_id") or kind
    return _append(root, record, {**extra, "kind": kind, "summary": str(summary), "basis": "torque_approval"})


def add_note(workspace: str | Path, client: str, identifier: str,
             text: str, kind: str = "note") -> dict:
    if kind not in ("note", "decision", "next_step"):
        raise ws.WorkspaceError("note kind must be note, decision or next_step")
    text = _text(text, "note")
    root, record = load_change(workspace, client, identifier)
    return _append(root, record, {"kind": kind, "summary": text, "basis": "operator_reported"})


def add_check(workspace: str | Path, client: str, identifier: str, criterion: str,
              result: str, summary: str, evidence: str | Path | None = None) -> dict:
    if result not in _RESULTS:
        raise ws.WorkspaceError(f"check result must be one of {', '.join(_RESULTS)}")
    summary = _text(summary, "check summary")
    root, record = load_change(workspace, client, identifier)
    if criterion not in {c["id"] for c in record["criteria"]}:
        raise ws.WorkspaceError("criterion must identify an acceptance criterion in this change")
    captured = _capture_file(root, evidence) if evidence is not None else None
    return _append(root, record, {"kind": "check", "criterion": criterion, "result": result,
                                 "summary": summary, "basis": "operator_reported", "evidence": captured})


def verify_deploy(workspace: str | Path, client: str, identifier: str, org: str,
                  job_id: str, components: list[str] | None = None,
                  manifest: str | Path | None = None) -> dict:
    """Read one exact live deployment; never turn metadata success into business acceptance."""
    root, record = load_change(workspace, client, identifier)
    if not _org(org):
        raise ws.WorkspaceError("org must be one explicit alias, username or ID")
    from jsc_qa.dispatcher import dispatch_meta_api
    copied = _capture_file(root, manifest) if manifest is not None else None
    result = dispatch_meta_api(org, record["title"], deploy_job_id=job_id,
                               deploy_components=components,
                               deploy_manifest=str(root / copied["path"]) if copied else None)
    observation = asdict(result)
    raw = observation.pop("raw_output", "")
    # Raw metadata output belongs only in private evidence, never default console text.
    evidence = None
    if raw:
        evidence_dir = ws._inside(root, root / "evidence")
        existed = evidence_dir.exists()
        evidence_dir.mkdir(mode=0o700, exist_ok=True)
        path = ws._inside(root, evidence_dir / (uuid4().hex + ".json"))
        ws.atomic_write_new(path, raw)
        ws.share_with_approver(_workspace_of(root), *([] if existed else [evidence_dir]), path)
        evidence = {"path": str(path.relative_to(root)), "name": "metadata-api-report.json",
                    "sha256": hashlib.sha256(raw.encode()).hexdigest(), "bytes": path.stat().st_size,
                    "meaning": "Metadata API report from this exact read; technical scope only."}
    return _append(root, record, {"kind": "metadata_observation", "summary": result.detail,
                                 "basis": "salesforce_metadata_api", "target_org": org,
                                 "job_id": job_id, "result": result.status.lower(),
                                 "requested_components": list(components or []),
                                 "observation": observation, "evidence": evidence,
                                 "manifest": copied,
                                 "business_acceptance_proven": False})


def _events(root: Path, record: dict) -> list[dict]:
    directory = ws._inside(root, root / "events")
    events = []
    if not directory.exists():
        return events
    for path in sorted(directory.glob("*.json")):
        event = ws._read_json(ws._inside(root, path))
        if (not _EVENT_ID.fullmatch(path.stem) or event.get("id") != path.stem
                or event.get("schema") != "torque.change-event/1" or event.get("change") != record["id"]
                or event.get("client") != record["client"] or not isinstance(event.get("summary"), str)
                or not event["summary"].strip() or not _timestamp(event.get("created_at"))
                or event.get("kind") not in ("note", "decision", "next_step", "check", "metadata_observation",
                                             *_TORQUE_BASIS)):
            raise ws.WorkspaceError(f"invalid change event: {path.name}")
        if event["kind"] == "check":
            if (event.get("result") not in _RESULTS or event.get("basis") != "operator_reported"
                    or event.get("criterion") not in {c["id"] for c in record["criteria"]}):
                raise ws.WorkspaceError(f"invalid reported acceptance check: {path.name}")
        if event["kind"] == "metadata_observation":
            if (event.get("basis") != "salesforce_metadata_api" or event.get("business_acceptance_proven") is not False
                    or not _org(event.get("target_org")) or not isinstance(event.get("job_id"), str)
                    or not event["job_id"].strip()
                    or event.get("result") not in ("pass", "fail", "error", "deferred", "manual_required", "skip_via_token")
                    or not isinstance(event.get("observation"), dict)
                    or not isinstance(event["observation"].get("metadata", {}), dict)):
                raise ws.WorkspaceError(f"invalid metadata observation: {path.name}")
            for components in (event.get("requested_components", []),
                               event["observation"].get("metadata", {}).get("expected_components", [])):
                if not isinstance(components, list) or any(not isinstance(c, str) for c in components):
                    raise ws.WorkspaceError(f"invalid metadata component scope: {path.name}")
        elif event["kind"] in _TORQUE_BASIS:
            if event.get("basis") != _TORQUE_BASIS[event["kind"]]:
                raise ws.WorkspaceError(f"invalid event provenance: {path.name}")
            if _missing_fields(event["kind"], event):
                raise ws.WorkspaceError(f"invalid approval event (missing fields): {path.name}")
            if event["kind"] == BEFORE_STATE_KIND and (
                    not isinstance(event.get("files"), list) or not isinstance(event.get("sha256"), str)
                    or not isinstance(event.get("path"), str)):
                raise ws.WorkspaceError(f"invalid before-state event: {path.name}")
        elif event.get("basis") != "operator_reported":
            raise ws.WorkspaceError(f"invalid event provenance: {path.name}")
        events.append(event)
    return events


def _integrity(root: Path, evidence: dict | None) -> str:
    if evidence is None:
        return "not_supplied"
    if (not isinstance(evidence, dict) or not isinstance(evidence.get("name"), str)
            or not isinstance(evidence.get("sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", evidence["sha256"])
            or type(evidence.get("bytes")) is not int or evidence["bytes"] < 0):
        raise ws.WorkspaceError("invalid evidence reference")
    relative = evidence.get("path")
    if (not isinstance(relative, str) or Path(relative).is_absolute()
            or Path(relative).parts[:1] != ("evidence",) or ".." in Path(relative).parts):
        raise ws.WorkspaceError("invalid evidence path")
    path = ws._inside(root, root / relative)
    try:
        digest = ws._file_hash(path)
        size = path.stat().st_size
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unavailable"
    return "matches_capture" if digest == evidence["sha256"] and size == evidence["bytes"] else "changed"


def get_change(workspace: str | Path, client: str, identifier: str) -> dict:
    root, record = load_change(workspace, client, identifier)
    events = _events(root, record)
    for event in events:
        if "evidence" in event:
            event["evidence_integrity"] = _integrity(root, event["evidence"])
        if "manifest" in event:
            event["manifest_integrity"] = _integrity(root, event["manifest"])
    criteria = []
    for criterion in record["criteria"]:
        checks = [e for e in events if e["kind"] == "check" and e["criterion"] == criterion["id"]]
        latest = checks[-1] if checks else None
        criteria.append({**criterion, "reported_result": latest["result"] if latest else "not_run",
                         "latest_check": latest["id"] if latest else None,
                         "evidence_integrity": latest.get("evidence_integrity") if latest else "not_supplied"})
    return {**record, "change_root": str(root), "events": events, "criteria": criteria,
            "assessment": {"criteria": len(criteria),
                           "reported_pass": sum(c["reported_result"] == "pass" for c in criteria),
                           "reported_fail": sum(c["reported_result"] == "fail" for c in criteria),
                           "not_yet_reported_pass": [c["id"] for c in criteria if c["reported_result"] != "pass"],
                           "evidence_problems": sum(e.get(key) in ("missing", "changed", "unavailable")
                                                    for e in events for key in ("evidence_integrity", "manifest_integrity")),
                           "business_acceptance_independently_verified": False},
            "next_steps": [e["summary"] for e in events if e["kind"] == "next_step"]}


def render_change(workspace: str | Path, client: str, identifier: str) -> str:
    item = get_change(workspace, client, identifier)
    lines = [f"# {item['title']}", "", f"Business outcome: {item['outcome']}", "",
             f"Change: {item['id']} · Client: {item['client']}",
             f"Planned org: {item.get('planned_org') or 'not specified'}", "", "## Acceptance criteria", ""]
    if not item["criteria"]:
        lines.append("No acceptance criteria recorded yet.")
    for criterion in item["criteria"]:
        lines.append(f"- {criterion['id']}: {criterion['text']} — **{criterion['reported_result'].replace('_', ' ')}** (reported)")
        if criterion["evidence_integrity"] in ("missing", "changed", "unavailable"):
            lines.append(f"  Evidence: {criterion['evidence_integrity']} since capture.")
    lines += ["", "These acceptance results are operator-reported. Metadata checks below prove only their stated technical scope."]
    for kind, heading in (("decision", "Decisions"), ("metadata_observation", "Metadata observations"),
                          ("check", "Acceptance evidence"), ("note", "Notes"), ("next_step", "Next steps")):
        events = [e for e in item["events"] if e["kind"] == kind]
        if not events:
            continue
        lines += ["", f"## {heading}", ""]
        for event in events:
            lines += [f"- {event['created_at']}: {event['summary']}"]
            if kind == "metadata_observation":
                lines += [f"  {event['result']} · Org {event['target_org']} · Job {event['job_id']}"]
                components = event["observation"].get("metadata", {}).get("expected_components") or event.get("requested_components") or []
                if components:
                    lines += ["  Expected components: " + ", ".join(components)]
                if event.get("manifest_integrity") in ("missing", "changed", "unavailable"):
                    lines += [f"  Deployment manifest: {event['manifest_integrity']} since capture."]
            elif kind == "check":
                lines += [f"  {event['criterion']}: {event['result']} (operator-reported)"]
            evidence = event.get("evidence")
            if evidence:
                path = Path(item["change_root"]) / evidence["path"]
                lines += [f"  Evidence: [{evidence['name']}](<{path}>) ({event['evidence_integrity']})",
                          f"  SHA-256: `{evidence['sha256']}`"]
    approvals = [e for e in item["events"] if e["kind"] in APPROVAL_KINDS or e["kind"] == BEFORE_STATE_KIND]
    if approvals:
        lines += ["", "## Approvals", ""]
        for event in approvals:
            ident = event.get("approval_id") or event.get("request_id") or ""
            org = event.get("org_alias") or ""
            lines.append(" ".join(part for part in (f"- {event['created_at']}", event["kind"], ident, org,
                                                    event["summary"]) if part))
    return "\n".join(lines).rstrip() + "\n"
