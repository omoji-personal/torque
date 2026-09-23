"""Create once; report command failures separately from incomplete evidence capture."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import sys
import time

from . import _common as c


_RECORD_ID = re.compile(r"[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?\Z")
_ERROR_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,79}\Z")


def _error_name(envelope: dict) -> str:
    """Expose only a short error identifier, never response messages, records or stacks."""
    candidates = [envelope.get("name"), envelope.get("errorCode"), envelope.get("code")]
    result = envelope.get("result")
    if isinstance(result, dict):
        errors = result.get("errors")
        if isinstance(errors, list):
            for error in errors:
                if isinstance(error, dict):
                    candidates.extend([error.get("statusCode"), error.get("errorCode")])
    return next((value for value in candidates if isinstance(value, str) and _ERROR_CODE.fullmatch(value)), "SF_CLI_ERROR")


def run(args: argparse.Namespace) -> int:
    wrapper_command = (
        f"jsc data create -o {args.target_org} --sobject {args.sobject} "
        f"--values '{args.values}'"
    )
    op = getattr(args, "operation_type", None) or "data_record_create"
    invoking_intent = None
    if getattr(args, "invoking_intent", None):
        invoking_intent = {"kind": "user-direct", "reason": args.invoking_intent, "token_fingerprint": None}
    if op == "revert" and getattr(args, "parent_snapshot_id", None):
        invoking_intent = {"kind": "revert-of", "reason": f"revert-of {args.parent_snapshot_id}", "token_fingerprint": None}
    ctx = c.WrapperContext(
        operation_type=op,
        target_org=args.target_org,
        wrapper_command=wrapper_command,
        invoking_intent=invoking_intent,
        parent_snapshot_id=getattr(args, "parent_snapshot_id", None),
    )
    rc = ctx.resolve_org()
    if rc: return rc
    rc = ctx.acquire_org_lock()
    if rc: return rc

    try:
        ctx.init_snapshot_dir()
        ctx.manifest["payload"] = {
            "object_api_name": args.sobject,
            "record_id": None,
            "external_id_field": None,
            "before_row": None,  # nothing pre-existed
            "after_row": None,
            "delete_mode": "not_applicable",
            "fields_captured": [],
        }
        ctx.set_revert_capabilities()
        ctx.update_phase("pre_snapshot", status="complete", duration_seconds=0.0)
        ctx.save()

        # ── Underlying ────────────────────────────────────────────────────
        t0 = time.monotonic()
        cmd = ["sf", "data", "create", "record",
               "--target-org", args.target_org,
               "--sobject", args.sobject,
               "--values", args.values, "--json"]
        exit_code, stdout, stderr = c.run_sf_subprocess(cmd)
        duration = round(time.monotonic() - t0, 2)
        (ctx.snap_dir / "underlying-result.json").write_text(stdout, encoding="utf-8")

        parsed = c.parse_sf_json_safely(stdout)
        envelope = parsed if isinstance(parsed, dict) else {}
        result = envelope.get("result")
        result = result if isinstance(result, dict) else {}
        envelope_status = envelope.get("status")
        reported_failure = (
            type(envelope_status) is int and envelope_status != 0
        ) or result.get("success") is False or bool(result.get("errors"))
        command_succeeded = exit_code == 0 and not reported_failure
        ctx.manifest["payload"]["create_command_succeeded"] = command_succeeded
        record_id = result.get("id")
        if not isinstance(record_id, str) or not _RECORD_ID.fullmatch(record_id):
            record_id = None
        ctx.manifest["payload"]["record_id"] = record_id
        ctx.manifest["payload"]["record_id_capture_status"] = "captured" if record_id else "missing_or_invalid"
        ctx.update_phase("underlying_command",
            status="complete" if command_succeeded else "failed", exit_code=exit_code,
            duration_seconds=duration, raw_artifact_paths=[str(ctx.snap_dir / "underlying-result.json")],
        )

        # Capture once after confirmed command success; missing evidence never triggers another create.
        t0 = time.monotonic()
        after_row = None
        if command_succeeded and record_id:
            after_row = _get_record(args.target_org, args.sobject, record_id, evidence_dir=ctx.snap_dir)
        ctx.manifest["payload"]["after_row"] = after_row
        captured = record_id is not None and after_row is not None
        ctx.manifest["payload"]["after_row_capture_status"] = "captured" if after_row is not None else "not_captured"
        ctx.manifest["payload"]["fields_captured"] = list(after_row) if after_row is not None else []
        ctx.manifest["snapshot_status"] = "failed" if not command_succeeded else ("complete" if captured else "partial")
        post_artifacts = [str(ctx.snap_dir / "post-create-result.json")] if command_succeeded and record_id else []
        ctx.update_phase("post_finalize",
            status="complete" if captured else "partial",
            duration_seconds=round(time.monotonic() - t0, 2), raw_artifact_paths=post_artifacts,
        )
        ctx.set_revert_capabilities()
        ctx.save()
        evidence_path = ctx.snap_dir / "manifest.json"
        if not command_succeeded:
            status = envelope_status if type(envelope_status) is int else "unknown"
            print(f"error: data create failed ({_error_name(envelope)}; sf_exit={exit_code}; status={status}). Snapshot evidence: {evidence_path}", file=sys.stderr)
            return c.EXIT_UNDERLYING_FAILED
        if not captured:
            missing = "created record ID" if not record_id else "after-row capture"
            print(f"warning: data create command succeeded, but {missing} is missing or invalid. Do not retry creation. Snapshot evidence: {evidence_path}", file=sys.stderr)
            return c.EXIT_POST_FINALIZE_FAILED
        print(f"created: {args.sobject} {record_id} (snapshot: {ctx.snapshot_id}). Snapshot evidence: {evidence_path}", file=sys.stderr)
        return c.EXIT_SUCCESS

    finally:
        ctx.release_lock()


def _get_record(target_org: str, sobject: str, record_id: str, *, evidence_dir: Path | None = None) -> dict | None:
    cmd = ["sf", "data", "get", "record",
           "--target-org", target_org, "--sobject", sobject, "--record-id", record_id, "--json"]
    code, stdout, _ = c.run_sf_subprocess(cmd, timeout_seconds=30)
    if evidence_dir is not None:
        (evidence_dir / "post-create-result.json").write_text(stdout, encoding="utf-8")
    if code != 0:
        return None
    try:
        envelope = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(envelope, dict) or envelope.get("status", 0) != 0:
        return None
    row = envelope.get("result")
    if not isinstance(row, dict):
        return None
    returned_id = row.get("Id")
    if not isinstance(returned_id, str) or not _RECORD_ID.fullmatch(returned_id) or returned_id[:15] != record_id[:15]:
        return None
    return row
