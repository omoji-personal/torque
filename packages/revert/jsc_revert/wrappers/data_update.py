"""jsc data update wrapper — sf data update record with before/after row capture."""

from __future__ import annotations

import argparse
import json
import sys
import time

from . import _common as c
from ..update_fields import parse_update_fields


def run(args: argparse.Namespace) -> int:
    wrapper_command = (
        f"jsc data update -o {args.target_org} --sobject {args.sobject} "
        f"--record-id {args.record_id} --values '{args.values}'"
    )
    op = getattr(args, "operation_type", None) or "data_record_update"
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

        # Pre: query current row state
        t0 = time.monotonic()
        before_row = _query_record(args.target_org, args.sobject, args.record_id)
        ctx.manifest["payload"] = {
            "object_api_name": args.sobject,
            "record_id": args.record_id,
            "external_id_field": None,
            "before_row": before_row,
            "after_row": None,
            "delete_mode": "not_applicable",
            "fields_captured": list(before_row.keys()) if before_row else [],
            "fields_updated": parse_update_fields(args.values),
        }
        ctx.set_revert_capabilities()
        ctx.update_phase("pre_snapshot",
            status="complete" if before_row is not None else "failed",
            duration_seconds=round(time.monotonic() - t0, 2),
        )
        ctx.save()

        if before_row is None and ctx.org.is_production:
            print(f"error: pre-snapshot failed (could not query record); aborting prod write", file=sys.stderr)
            ctx.manifest["snapshot_status"] = "failed"
            ctx.save()
            return c.EXIT_PRESNAP_FAILED_PROD

        # ── Underlying ────────────────────────────────────────────────────
        t0 = time.monotonic()
        cmd = ["sf", "data", "update", "record",
               "--target-org", args.target_org,
               "--sobject", args.sobject,
               "--record-id", args.record_id,
               "--values", args.values, "--json"]
        exit_code, stdout, stderr = c.run_sf_subprocess(cmd)
        duration = round(time.monotonic() - t0, 2)
        (ctx.snap_dir / "underlying-result.json").write_text(stdout, encoding="utf-8")

        snapshot_status = "complete" if exit_code == 0 else "failed"
        ctx.manifest["snapshot_status"] = snapshot_status
        ctx.update_phase("underlying_command",
            status=snapshot_status, exit_code=exit_code, duration_seconds=duration,
            raw_artifact_paths=[str(ctx.snap_dir / "underlying-result.json")],
        )

        # Post: re-query record
        t0 = time.monotonic()
        after_row = _query_record(args.target_org, args.sobject, args.record_id)
        ctx.manifest["payload"]["after_row"] = after_row
        ctx.update_phase("post_finalize",
            status="complete" if after_row is not None else "failed",
            duration_seconds=round(time.monotonic() - t0, 2),
        )
        ctx.save()

        return c.EXIT_SUCCESS if exit_code == 0 else c.EXIT_UNDERLYING_FAILED

    finally:
        ctx.release_lock()


def _query_record(target_org: str, sobject: str, record_id: str) -> dict | None:
    """Query a single record by ID. Returns dict of fields or None on failure."""
    cmd = ["sf", "data", "get", "record",
           "--target-org", target_org,
           "--sobject", sobject,
           "--record-id", record_id, "--json"]
    code, stdout, _ = c.run_sf_subprocess(cmd, timeout_seconds=30)
    if code != 0:
        return None
    try:
        data = json.loads(stdout)
        return data.get("result", {})
    except json.JSONDecodeError:
        return None
