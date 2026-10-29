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
from pathlib import Path, PurePosixPath, PureWindowsPath
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


def _directory(workspace: str | Path, client: str, engagement_kind: str = "client") -> tuple[Path, dict]:
    root, _, config = ws.load_engagement(workspace, client, engagement_kind)
    return ws._inside(root, root / "changes"), config


def _owner_key(record: dict) -> str:
    return "initiative" if "initiative" in record else "client"


def create_change(workspace: str | Path, client: str, title: str, outcome: str,
                  criteria: list[str] | None = None, org: str | None = None, *,
                  engagement_kind: str = "client") -> dict:
    title, outcome = _text(title, "title"), _text(outcome, "business outcome")
    criteria = [_text(c, "acceptance criterion") for c in (criteria or [])]
    if org is not None:
        from .engagements import require
        require(engagement_kind, "org")
        if not _org(org):
            raise ws.WorkspaceError("org must be one explicit alias, username or ID")
    directory, config = _directory(workspace, client, engagement_kind)
    ws.require_engagement_writable(directory.parent)
    identifier = "chg-" + uuid4().hex[:12]
    record = {"schema": "torque.change/1", "id": identifier, engagement_kind: config["slug"],
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
        ws._write_json(pending / "change.json", record, private=True)
        # V2 I6: the delegated approver reads change records.
        ws.share_with_approver(workspace, *([] if existed else [directory]), pending, pending / "events",
                               pending / "evidence", pending / "change.json")
        pending.rename(directory / identifier)
    finally:
        if pending.exists():
            shutil.rmtree(pending)
    return record


def load_change(workspace: str | Path, client: str, identifier: str, *,
                engagement_kind: str = "client") -> tuple[Path, dict]:
    if not isinstance(identifier, str) or not _CHANGE_ID.fullmatch(identifier):
        raise ws.WorkspaceError("change ID must be an ID returned by torque change create/list")
    directory, config = _directory(workspace, client, engagement_kind)
    root = ws._inside(directory, directory / identifier)
    missing = (f"no change {identifier} for this {engagement_kind} ({config['slug']}); list its changes with "
               + ws.command_text(["torque", "change", "list", "--workspace", str(directory.parent.parent.parent),
                                  "--" + engagement_kind, config["slug"]]))
    record = ws._read_json(ws._inside(directory, root / "change.json"), missing)
    if ws.foreign_owner(record, engagement_kind, config["slug"]):
        raise ws.BoundaryError(f"change record belongs to a different client or initiative: {identifier}")
    if (record.get("schema") != "torque.change/1" or record.get("id") != identifier
            or record.get(engagement_kind) != config["slug"] or not isinstance(record.get("title"), str)
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


def list_changes(workspace: str | Path, client: str, *, engagement_kind: str = "client",
                 problems: list | None = None) -> list[dict]:
    """The engagement's changes, newest first. A change record that fails its checks
    stops the read, unless `problems` is a list: then it is skipped and named there
    (as ws.list_sessions does). Approval and recovery code reads strictly."""
    directory, _ = _directory(workspace, client, engagement_kind)
    if not directory.exists():
        return []
    records = []
    for p in directory.iterdir():
        if not _CHANGE_ID.fullmatch(p.name):
            continue
        try:
            records.append(load_change(workspace, client, p.name, engagement_kind=engagement_kind)[1])
        except ws.BoundaryError:
            raise
        except ws.WorkspaceError as exc:
            if problems is None:
                raise
            reason = str(exc) if (p / "change.json").exists() else "the change folder has no change.json"
            problems.append(ws.record_problem(directory.parent, p / "change.json", reason))
    return sorted(records, key=lambda c: (c["created_at"], c["id"]), reverse=True)


def _capture_file(root: Path, source: str | Path) -> dict:
    ws.require_engagement_writable(root.parent.parent)
    raw = Path(source).expanduser().absolute()
    if raw.is_symlink():
        raise ws.WorkspaceError("evidence source must not be a symlink")
    path = raw.resolve()
    # This change's engagement may use shared firm artifacts, but never another engagement.
    if ws.foreign_engagement(root.parent.parent, path):
        raise ws.WorkspaceError("evidence belongs to a different client or initiative")
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
    ws.require_engagement_writable(root.parent.parent)
    directory = ws._inside(root, root / "events")
    existed = directory.exists()
    directory.mkdir(mode=0o700, exist_ok=True)
    at = datetime.now(timezone.utc)
    identifier = at.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:12]
    value = {"schema": "torque.change-event/1", "id": identifier,
             "change": record["id"], _owner_key(record): record[_owner_key(record)],
             "created_at": at.isoformat(), **event}
    ws._write_json(ws._inside(root, directory / f"{identifier}.json"), value, private=True)
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


def append_approval_event(workspace: str | Path, client: str, change_id: str, kind: str, fields: dict, *,
                          engagement_kind: str = "client") -> dict:
    """Record one approval step. These events log what happened; they do not
    authorize anything (the approval store and the gate do)."""
    from .engagements import require
    require(engagement_kind, "approvals")
    if kind not in APPROVAL_KINDS:
        raise ws.WorkspaceError(f"unknown approval event kind: {kind}")
    missing = _missing_fields(kind, fields)
    if missing:
        raise ws.WorkspaceError(f"{kind} event is missing: {', '.join(missing)}")
    root, record = load_change(workspace, client, change_id, engagement_kind=engagement_kind)
    reserved = {"schema", "id", "change", "client", "created_at", "kind", "basis", "summary"}
    extra = {k: v for k, v in fields.items() if k not in reserved}
    summary = fields.get("command") or fields.get("reason") or fields.get("approval_id") or kind
    return _append(root, record, {**extra, "kind": kind, "summary": str(summary), "basis": "torque_approval"})


def add_note(workspace: str | Path, client: str, identifier: str,
             text: str, kind: str = "note", *, engagement_kind: str = "client") -> dict:
    if kind not in ("note", "decision", "next_step"):
        raise ws.WorkspaceError("note kind must be note, decision or next_step")
    text = _text(text, "note")
    root, record = load_change(workspace, client, identifier, engagement_kind=engagement_kind)
    return _append(root, record, {"kind": kind, "summary": text, "basis": "operator_reported"})


def add_check(workspace: str | Path, client: str, identifier: str, criterion: str,
              result: str, summary: str, evidence: str | Path | None = None, *,
              engagement_kind: str = "client") -> dict:
    if result not in _RESULTS:
        raise ws.WorkspaceError(f"check result must be one of {', '.join(_RESULTS)}")
    summary = _text(summary, "check summary")
    root, record = load_change(workspace, client, identifier, engagement_kind=engagement_kind)
    if criterion not in {c["id"] for c in record["criteria"]}:
        raise ws.WorkspaceError("criterion must identify an acceptance criterion in this change")
    captured = _capture_file(root, evidence) if evidence is not None else None
    return _append(root, record, {"kind": "check", "criterion": criterion, "result": result,
                                 "summary": summary, "basis": "operator_reported", "evidence": captured})


def verify_deploy(workspace: str | Path, client: str, identifier: str, org: str,
                  job_id: str, components: list[str] | None = None,
                  manifest: str | Path | None = None, *, engagement_kind: str = "client") -> dict:
    """Read one exact live deployment; never turn metadata success into business acceptance."""
    from .engagements import require
    require(engagement_kind, "verify_deploy")
    root, record = load_change(workspace, client, identifier, engagement_kind=engagement_kind)
    ws.require_writable(_workspace_of(root))
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
        ws.atomic_write_new(path, raw, private=True)
        ws.share_with_approver(_workspace_of(root), *([] if existed else [evidence_dir]), path)
        # Forward slashes, as every other evidence path: a record read on another system stays valid.
        evidence = {"path": path.relative_to(root).as_posix(), "name": "metadata-api-report.json",
                    "sha256": hashlib.sha256(raw.encode()).hexdigest(), "bytes": path.stat().st_size,
                    "meaning": "Metadata API report from this exact read; technical scope only."}
    return _append(root, record, {"kind": "metadata_observation", "summary": result.detail,
                                 "basis": "salesforce_metadata_api", "target_org": org,
                                 "job_id": job_id, "result": result.status.lower(),
                                 "requested_components": list(components or []),
                                 "observation": observation, "evidence": evidence,
                                 "manifest": copied,
                                 "business_acceptance_proven": False})


def _events(root: Path, record: dict, problems: list | None = None) -> list[dict]:
    """The change's events in order. An event that fails its checks stops the read,
    unless `problems` is a list: then it is skipped and named there. An event that
    names another engagement, and a symlink, stop the read either way."""
    directory = ws._inside(root, root / "events")
    events = []
    if not directory.exists():
        return events
    for path in sorted(directory.glob("*.json")):
        try:
            events.append(_event(root, record, path))
        except ws.BoundaryError:
            raise
        except ws.WorkspaceError as exc:
            if problems is None:
                raise
            problems.append(ws.record_problem(root.parent.parent, path, str(exc)))
    return events


def _event(root: Path, record: dict, path: Path) -> dict:
    """One checked change event. Every field is checked for its type before it is
    used, so a damaged event raises WorkspaceError; one that names another engagement
    or a path out of the change's evidence folder raises BoundaryError."""
    event = ws._read_json(ws._inside(root, path))
    if ws.foreign_owner(event, _owner_key(record), record[_owner_key(record)]):
        raise ws.BoundaryError(f"change event belongs to a different client or initiative: {path.name}")
    # A path out of the evidence folder stops the read, however damaged the rest is.
    for key in ("evidence", "manifest"):
        if isinstance(event.get(key), dict):
            _refuse_escape(event[key].get("path"))
    if event.get("kind") == BEFORE_STATE_KIND:
        _refuse_escape(event.get("path"))
        for item in event.get("files") if isinstance(event.get("files"), list) else []:
            if isinstance(item, dict):
                _refuse_escape(item.get("path"))
    if (not _EVENT_ID.fullmatch(path.stem) or event.get("id") != path.stem
            or event.get("schema") != "torque.change-event/1" or event.get("change") != record["id"]
            or event.get(_owner_key(record)) != record[_owner_key(record)] or not isinstance(event.get("summary"), str)
            or not event["summary"].strip() or not _timestamp(event.get("created_at"))
            or event.get("kind") not in ("note", "decision", "next_step", "check", "metadata_observation",
                                         *_TORQUE_BASIS)):
        raise ws.WorkspaceError(f"invalid change event: {path.name}")
    if event["kind"] == "check":
        if (event.get("result") not in _RESULTS or event.get("basis") != "operator_reported"
                or not isinstance(event.get("criterion"), str)
                or event["criterion"] not in {c["id"] for c in record["criteria"]}):
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
        # The identifiers a handoff prints are text, or absent.
        if any(event.get(key) is not None and not isinstance(event[key], str)
               for key in ("approval_id", "request_id", "org_alias")):
            raise ws.WorkspaceError(f"invalid approval event (a field has the wrong type): {path.name}")
        if event["kind"] == BEFORE_STATE_KIND and (
                not isinstance(event.get("files"), list) or not isinstance(event.get("sha256"), str)
                or not isinstance(event.get("path"), str)
                or any(not isinstance(item, dict) or not isinstance(item.get("path"), str)
                       or not isinstance(item.get("sha256"), str) for item in event["files"])):
            raise ws.WorkspaceError(f"invalid before-state event: {path.name}")
    elif event.get("basis") != "operator_reported":
        raise ws.WorkspaceError(f"invalid event provenance: {path.name}")
    return event


_LEAVES = "invalid evidence path: it leads out of the change's evidence folder"


def _refuse_escape(value: object) -> None:
    """BoundaryError when a recorded path inside a change folder is absolute, names a
    drive or has a `..` part: it would lead out of the change's evidence folder, and
    every reader refuses that. Any other value is left to the field checks."""
    if isinstance(value, str) and (value.startswith(("/", "\\")) or PureWindowsPath(value).drive
                                   or ".." in re.split(r"[\\/]", value)):
        raise ws.BoundaryError(_LEAVES)


def _evidence_path(value: object) -> PurePosixPath:
    """A captured file's path inside its change folder (evidence/NAME). A path that
    leads out of the evidence folder raises BoundaryError (_refuse_escape); any other
    malformed value raises WorkspaceError, which an informational reader skips as a
    damaged record. A record that versions up to 2.0.0a21 wrote on Windows can say
    evidence\\NAME; it is read the same on every system."""
    _refuse_escape(value)
    if not isinstance(value, str) or not value or "\0" in value:
        raise ws.WorkspaceError("invalid evidence path")
    relative = PurePosixPath(value.replace("\\", "/"))
    if relative.parts[:1] != ("evidence",):
        raise ws.WorkspaceError("invalid evidence path")
    return relative


def _integrity(root: Path, evidence: dict | None) -> str:
    if evidence is None:
        return "not_supplied"
    if not isinstance(evidence, dict):
        raise ws.WorkspaceError("invalid evidence reference")
    # A path that leads out of the folder stops the read before anything else is checked.
    relative = _evidence_path(evidence.get("path"))
    if (not isinstance(evidence.get("name"), str)
            or not isinstance(evidence.get("sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", evidence["sha256"])
            or type(evidence.get("bytes")) is not int or evidence["bytes"] < 0):
        raise ws.WorkspaceError("invalid evidence reference")
    path = ws._inside(root, root.joinpath(*relative.parts))
    try:
        digest = ws._file_hash(path)
        size = path.stat().st_size
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unavailable"
    return "matches_capture" if digest == evidence["sha256"] and size == evidence["bytes"] else "changed"


def get_change(workspace: str | Path, client: str, identifier: str, *, engagement_kind: str = "client",
               problems: list | None = None) -> dict:
    """The change with its checked events. With a `problems` list a damaged or stray
    event is skipped and named there (context, handoff, doctor and `change show`);
    without it, as approval and recovery code calls it, the read stops."""
    root, record = load_change(workspace, client, identifier, engagement_kind=engagement_kind)
    events = []
    for event in _events(root, record, problems):
        try:
            if "evidence" in event:
                event["evidence_integrity"] = _integrity(root, event["evidence"])
            if "manifest" in event:
                event["manifest_integrity"] = _integrity(root, event["manifest"])
        except ws.BoundaryError:
            raise
        except ws.WorkspaceError as exc:
            if problems is None:
                raise
            problems.append(ws.record_problem(root.parent.parent, root / "events" / f"{event['id']}.json", str(exc)))
            continue
        events.append(event)
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


def render_change(workspace: str | Path, client: str, identifier: str, *, engagement_kind: str = "client",
                  problems: list | None = None) -> str:
    item = get_change(workspace, client, identifier, engagement_kind=engagement_kind, problems=problems)
    owner_label = "Client" if engagement_kind == "client" else "Initiative"
    lines = [f"# {item['title']}", "", f"Business outcome: {item['outcome']}", "",
             f"Change: {item['id']} · {owner_label}: {item[engagement_kind]}",
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
                path = Path(item["change_root"]).joinpath(*_evidence_path(evidence["path"]).parts)
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
