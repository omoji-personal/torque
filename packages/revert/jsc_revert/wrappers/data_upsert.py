"""Single-record atomic upsert through Salesforce's sObject Collections API.

The external ID is always part of the one-record request. A pre-query captures
before-state only; it never decides whether to create or update. Recovery remains
manual because automation and concurrent remote edits cannot be rolled back here.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time

from . import _common as c
from .data_update import _query_record
from ..update_fields import parse_assignments

PRE_FOUND = "found"
PRE_NOT_FOUND = "not_found"
PRE_ERROR = "error"
_FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_RECORD_ID = re.compile(r"[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?\Z")


def _parse_values(values: str) -> dict[str, str]:
    """Parse quoted assignments once, without expansion or command execution."""
    result, names = {}, {}
    assignments = parse_assignments(values) if values.strip() else []
    if assignments is None:
        raise ValueError("--values contains malformed Field=Value assignments")
    for field, value in assignments:
        if field.lower() in {"id", "attributes"}:
            raise ValueError("--values requires writable Field=Value assignments")
        previous = names.get(field.lower())
        if previous is not None and result[previous] != value:
            raise ValueError(f"Conflicting assignments for {field}")
        names[field.lower()] = previous or field
        result[previous or field] = value
    return result


def _sf_value(value: str):
    """Preserve the CLI's boolean and JSON relationship-value conversions."""
    if "{" in value and "}" in value:
        try:
            return json.loads(value)
        except ValueError:
            pass
    if value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    return value


def _merge_external_id(spec: str, values: dict[str, str]) -> tuple[str, str]:
    field, equals, value = spec.partition("=")
    field = field.strip()
    if not _FIELD.fullmatch(field) or field.lower() in {"id", "attributes"}:
        raise ValueError("--external-id requires an external-ID field name")
    existing = next((key for key in values if key.lower() == field.lower()), None)
    if not equals:
        value = values.get(existing) if existing else None
    if value is None or not value.strip():
        raise ValueError("The external ID must have a nonempty value")
    if existing is not None:
        if values[existing] != value:
            raise ValueError("--external-id conflicts with --values")
        del values[existing]
    values[field] = value
    return field, value


def run(args: argparse.Namespace) -> int:
    try:
        if not _FIELD.fullmatch(args.sobject):
            raise ValueError("Invalid sObject API name")
        values = _parse_values(args.values)
        field, value = _merge_external_id(args.external_id, values)
    except ValueError as exc:
        print(f"error: {exc}; nothing was run", file=sys.stderr)
        return 2
    ctx = c.WrapperContext(operation_type="data_record_upsert", target_org=args.target_org,
                           wrapper_command=f"jsc data upsert -o {args.target_org} --sobject {args.sobject} "
                           f"--external-id {args.external_id} --values '{args.values}'")
    rc = ctx.resolve_org()
    if rc: return rc
    rc = ctx.acquire_org_lock()
    if rc: return rc
    try:
        ctx.init_snapshot_dir()
        t0 = time.monotonic()
        pre_result, before_row, before_id = _query_by_external_id_tagged(args.target_org, args.sobject, field, value)
        ctx.manifest["payload"] = {
            "object_api_name": args.sobject, "external_id_field": field, "external_id_value": value,
            "pre_query_result": pre_result, "before_row": before_row, "before_record_id": before_id,
            "after_row": None, "after_record_id": None, "upsert_mode": None,
            "fields_captured": list(before_row) if before_row else [],
        }
        ctx.set_revert_capabilities()
        ctx.update_phase("pre_snapshot", status="failed" if pre_result == PRE_ERROR else "complete",
                         duration_seconds=round(time.monotonic() - t0, 2))
        ctx.save()
        if pre_result == PRE_ERROR:
            ctx.manifest["snapshot_status"] = "failed"
            ctx.save()
            print("error: upsert lookup is indeterminate; nothing was written. Fix capture before retrying.", file=sys.stderr)
            return c.EXIT_PRESNAP_FAILED_PROD

        request = ctx.snap_dir / "upsert-request.json"
        record = {key: _sf_value(item) for key, item in values.items() if key != field}
        record[field] = value  # The external-ID assignment is a literal key.
        c.bundle.atomic_write_json(request, {"allOrNone": True, "records": [
            {"attributes": {"type": args.sobject}, **record}]})
        cmd = ["sf", "api", "request", "rest",
               f"/services/data/v66.0/composite/sobjects/{args.sobject}/{field}",
               "--target-org", args.target_org, "--method", "PATCH",
               "--header", "Content-Type: application/json", "--body", "@" + str(request), "--json"]
        t0 = time.monotonic()
        code, stdout, stderr = c.run_sf_subprocess(cmd, timeout_seconds=60)
        raw_path = ctx.snap_dir / "underlying-result.json"
        c.bundle.atomic_write_text(raw_path, stdout)
        result = _upsert_result(stdout)
        succeeded = code == 0 and result is not None and result.get("success") is True
        status = "complete" if succeeded else "failed" if code > 0 or (result and result.get("success") is False) else "partial"
        ctx.update_phase("underlying_command", status=status, exit_code=code,
                         duration_seconds=round(time.monotonic() - t0, 2), raw_artifact_paths=[str(raw_path)])
        after_id = result.get("id") if succeeded else None
        if not isinstance(after_id, str) or not _RECORD_ID.fullmatch(after_id):
            after_id = None
        after_row = _query_record_by_id(args.target_org, args.sobject, after_id) if after_id else None
        ctx.manifest["payload"].update(after_row=after_row, after_record_id=after_id,
            upsert_mode=("insert" if result.get("created") else "update") if succeeded and type(result.get("created")) is bool else None)
        ctx.update_phase("post_finalize", status="complete" if after_row is not None else "failed")
        return c.finish_record_capture(ctx, status, before_ok=True, after_ok=after_row is not None)
    finally:
        ctx.release_lock()


def _upsert_result(stdout: str) -> dict | None:
    try:
        data = json.loads(stdout)
        response = data["result"]
        if data.get("status", 0) != 0 or response.get("statusCode") != 200:
            return None
        body = response.get("body")
        if isinstance(body, str):
            body = json.loads(body)
        if isinstance(body, list) and len(body) == 1 and isinstance(body[0], dict):
            return body[0]
    except (ValueError, KeyError, TypeError, AttributeError):
        pass
    return None


def _query_by_external_id_tagged(target_org, sobject, field, value):
    if not isinstance(field, str) or not _FIELD.fullmatch(field) or not _FIELD.fullmatch(sobject) or value is None:
        return PRE_ERROR, None, None
    safe_value = value.replace("\\", "\\\\").replace("'", "\\'")
    command = ["sf", "data", "query", "--target-org", target_org, "--query",
               f"SELECT FIELDS(ALL) FROM {sobject} WHERE {field}='{safe_value}' LIMIT 2", "--json"]
    code, stdout, _ = c.run_sf_subprocess(command, timeout_seconds=30)
    if code != 0:
        return PRE_ERROR, None, None
    try:
        data = json.loads(stdout)
        result = data["result"]
        records = result["records"]
        if (data.get("status", 0) != 0 or result.get("done") is not True
                or not isinstance(records, list) or type(result.get("totalSize")) is not int
                or result["totalSize"] != len(records) or len(records) > 1):
            return PRE_ERROR, None, None
        if not records:
            return PRE_NOT_FOUND, None, None
        row = records[0]
        rid = row.get("Id")
        if not isinstance(rid, str) or not _RECORD_ID.fullmatch(rid):
            return PRE_ERROR, None, None
        return PRE_FOUND, row, rid
    except (ValueError, KeyError, TypeError, AttributeError):
        return PRE_ERROR, None, None


def _query_record_by_id(target_org: str, sobject: str, record_id: str) -> dict | None:
    return _query_record(target_org, sobject, record_id)
