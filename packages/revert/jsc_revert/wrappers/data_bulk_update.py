"""jsc data update bulk wrapper — sf data update bulk with before-row CSV capture.

Phase I.4-extended-2 (v7.17.0). Bulk update: query the before-state of every
record-id in the input CSV BEFORE the bulk run; store as before_csv. Revert is
"reapply before_csv as a new bulk update."
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
from pathlib import Path

from . import _common as c

PAGE_SIZE_FOR_REFS = 200  # not used here; placeholder for symmetry with bulk_delete


def run(args: argparse.Namespace) -> int:
    wrapper_command = (
        f"jsc data update bulk -o {args.target_org} --sobject {args.sobject} "
        f"--file {args.file}"
    )
    ctx = c.WrapperContext(
        operation_type="data_bulk_update",
        target_org=args.target_org,
        wrapper_command=wrapper_command,
    )
    rc = ctx.resolve_org()
    if rc: return rc
    rc = ctx.acquire_org_lock()
    if rc: return rc

    try:
        ctx.init_snapshot_dir()

        # Pre: parse input CSV → extract record IDs → bulk-query before-state
        t0 = time.monotonic()
        input_path = Path(args.file)
        if not input_path.exists():
            ctx.manifest["snapshot_status"] = "failed"
            ctx.update_phase("pre_snapshot",
                status="failed",
                duration_seconds=round(time.monotonic() - t0, 2),
                error="input file not found",
            )
            ctx.save()
            return c.EXIT_PRESNAP_FAILED_PROD if ctx.org.is_production else c.EXIT_PRESNAP_FAILED_SANDBOX

        record_ids, fields = _parse_bulk_csv(input_path)
        before_csv_path = ctx.snap_dir / "before.csv"
        before_count = _bulk_query_before(
            args.target_org, args.sobject, record_ids, fields, before_csv_path
        )
        ctx.manifest["payload"] = {
            "object_api_name": args.sobject,
            "input_file": str(input_path),
            "input_record_count": len(record_ids),
            "before_csv": str(before_csv_path) if before_count else None,
            "before_record_count": before_count,
            "fields_captured": fields,
            "bulk_job_id": None,
        }
        ctx.set_revert_capabilities()
        ctx.update_phase("pre_snapshot",
            status="complete" if before_count else "failed",
            duration_seconds=round(time.monotonic() - t0, 2),
        )
        ctx.save()

        # Codex-R1-P1-05: fail-closed in production if paging short
        if ctx.org.is_production and before_count < len(record_ids):
            print(
                f"error: before-state capture short ({before_count}/{len(record_ids)} rows) "
                f"on production org {ctx.org.alias!r}; refusing bulk update without "
                f"complete forensic CSV.",
                file=sys.stderr,
            )
            return c.EXIT_PRESNAP_FAILED_PROD

        # ── Underlying ────────────────────────────────────────────────────
        t0 = time.monotonic()
        wait_min = getattr(args, "wait", 30)
        timeout_s = wait_min * 60 + 60
        rc = c.assert_lock_safe_or_opt_in(timeout_s)
        if rc:
            return rc
        cmd = ["sf", "data", "update", "bulk",
               "--target-org", args.target_org,
               "--sobject", args.sobject,
               "--file", str(input_path),
               "--line-ending", c.csv_line_ending(input_path),
               "--wait", str(wait_min),
               "--json"]
        exit_code, stdout, stderr = c.run_sf_subprocess(cmd, timeout_seconds=timeout_s)
        duration = round(time.monotonic() - t0, 2)
        (ctx.snap_dir / "underlying-result.json").write_text(stdout)
        c.bundle.atomic_write_json(ctx.snap_dir / "underlying-command.json",
            {"command": cmd, "exit_code": exit_code, "stdout": stdout, "stderr": stderr})
        sf_json = c.parse_sf_json_safely(stdout)

        # Codex-R1-P1-04: bulk-specific classifier (not deploy classifier)
        snap_status, wrapper_exit, job_id = c.capture_bulk_outcome(ctx, sf_json, exit_code)
        ctx.manifest["payload"]["bulk_job_id"] = job_id
        ctx.manifest["snapshot_status"] = snap_status
        ctx.update_phase("underlying_command",
            status=snap_status, exit_code=exit_code, duration_seconds=duration,
            raw_artifact_paths=[str(ctx.snap_dir / "underlying-result.json")],
        )

        # Codex-R1-P1-02: auto-enqueue if pending
        if snap_status == "pending_finalize_required" and job_id:
            c.auto_enqueue_if_pending(ctx, snap_status, job_id,
                ["sf", "data", "update", "resume", "--target-org", args.target_org,
                 "--job-id", job_id, "--json"])

        # Post: no further query (bulk job IDs are the durable handle)
        ctx.update_phase("post_finalize",
            status="complete",
            duration_seconds=0.0,
        )
        ctx.save()

        return wrapper_exit

    finally:
        ctx.release_lock()


def _parse_bulk_csv(path: Path) -> tuple[list[str], list[str]]:
    """Parse a bulk-update CSV; return (record_ids, all_field_names)."""
    text = path.read_text()
    reader = csv.DictReader(io.StringIO(text))
    fields = list(reader.fieldnames or [])
    ids: list[str] = []
    for row in reader:
        rid = row.get("Id") or row.get("ID") or row.get("id")
        if rid:
            ids.append(rid)
    return ids, fields


PAGE_SIZE = 200  # SOQL IN clause safe size; well under 1000-element platform limit


def _bulk_query_before(
    target_org: str, sobject: str, record_ids: list[str],
    fields: list[str], out_path: Path,
) -> int:
    """Bulk-query before-state of EVERY id; write as CSV. Returns count written.

    Codex-R1-P1-05 closure: page through all input IDs (no silent 1000 cap).
    Fail-closed (returns 0) if any chunk fails — caller's prod-fail-closed
    gate then refuses the operation.
    """
    if not record_ids or not fields:
        return 0
    field_list = ",".join(fields)
    all_records: list[dict] = []
    for chunk_start in range(0, len(record_ids), PAGE_SIZE):
        chunk = record_ids[chunk_start:chunk_start + PAGE_SIZE]
        id_list = ",".join(f"'{i}'" for i in chunk)
        query = f"SELECT {field_list} FROM {sobject} WHERE Id IN ({id_list})"
        cmd = ["sf", "data", "query",
               "--target-org", target_org,
               "--query", query, "--json"]
        code, stdout, _ = c.run_sf_subprocess(cmd, timeout_seconds=180)
        if code != 0:
            return 0  # fail-closed
        try:
            data = json.loads(stdout)
            chunk_records = data.get("result", {}).get("records", [])
        except json.JSONDecodeError:
            return 0
        all_records.extend(chunk_records)
    if not all_records:
        return 0
    with out_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in all_records:
            row = {k: r.get(k, "") for k in fields}
            w.writerow(row)
    return len(all_records)
