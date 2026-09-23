"""jsc data import bulk wrapper — sf data import bulk with CSV-row pre-snapshot.

Phase I.4-extended-2 (v7.17.0). Bulk import inserts N rows from a CSV. Best-
effort revert: capture post-insert IDs from sf JSON output; revert is bulk-delete
of those IDs.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import time
from pathlib import Path

from . import _common as c


def run(args: argparse.Namespace) -> int:
    wrapper_command = (
        f"jsc data import bulk -o {args.target_org} --sobject {args.sobject} "
        f"--file {args.file}"
    )
    ctx = c.WrapperContext(
        operation_type="data_bulk_import",
        target_org=args.target_org,
        wrapper_command=wrapper_command,
    )
    rc = ctx.resolve_org()
    if rc: return rc
    rc = ctx.acquire_org_lock()
    if rc: return rc

    try:
        ctx.init_snapshot_dir()
        t0 = time.monotonic()
        input_path = Path(args.file)
        if not input_path.exists():
            ctx.manifest["snapshot_status"] = "failed"
            ctx.update_phase("pre_snapshot", status="failed",
                duration_seconds=round(time.monotonic() - t0, 2),
                error="input file not found")
            ctx.save()
            return c.EXIT_PRESNAP_FAILED_PROD if ctx.org.is_production else c.EXIT_PRESNAP_FAILED_SANDBOX

        # Pre: copy input CSV into snapshot for forensics
        snap_input = ctx.snap_dir / "input.csv"
        snap_input.write_bytes(input_path.read_bytes())
        ctx.manifest["payload"] = {
            "object_api_name": args.sobject,
            "input_file": str(input_path),
            "input_snapshot_path": str(snap_input),
            "input_row_count": _csv_row_count(input_path),
            "inserted_record_ids": [],
            "bulk_job_id": None,
        }
        ctx.set_revert_capabilities()
        ctx.update_phase("pre_snapshot", status="complete",
            duration_seconds=round(time.monotonic() - t0, 2))
        ctx.save()

        t0 = time.monotonic()
        wait_min = getattr(args, "wait", 30)
        timeout_s = wait_min * 60 + 60
        rc = c.assert_lock_safe_or_opt_in(timeout_s)
        if rc:
            return rc
        cmd = ["sf", "data", "import", "bulk",
               "--target-org", args.target_org,
               "--sobject", args.sobject,
               "--file", str(input_path),
               "--line-ending", c.csv_line_ending(input_path),
               "--wait", str(wait_min),
               "--json"]
        exit_code, stdout, stderr = c.run_sf_subprocess(cmd, timeout_seconds=timeout_s)
        duration = round(time.monotonic() - t0, 2)
        (ctx.snap_dir / "underlying-result.json").write_text(stdout, encoding="utf-8")
        c.bundle.atomic_write_json(ctx.snap_dir / "underlying-command.json",
            {"command": cmd, "exit_code": exit_code, "stdout": stdout, "stderr": stderr})
        sf_json = c.parse_sf_json_safely(stdout)
        snap_status, wrapper_exit, job_id = c.capture_bulk_outcome(ctx, sf_json, exit_code)
        ctx.manifest["payload"]["bulk_job_id"] = job_id
        ctx.manifest["snapshot_status"] = snap_status
        ctx.update_phase("underlying_command",
            status=snap_status, exit_code=exit_code, duration_seconds=duration,
            raw_artifact_paths=[str(ctx.snap_dir / "underlying-result.json"),
                                str(snap_input)])
        if snap_status == "pending_finalize_required" and job_id:
            c.auto_enqueue_if_pending(ctx, snap_status, job_id,
                ["sf", "data", "import", "resume", "--target-org", args.target_org,
                 "--job-id", job_id, "--json"])
        # Codex-R1-P1-06: fetch inserted IDs via bulk results
        if snap_status in ("complete", "applied_partial") and job_id:
            success_csv = ctx.snap_dir / "after_success.csv"
            if _fetch_bulk_results(args.target_org, job_id, success_csv):
                ctx.manifest["payload"]["after_success_csv"] = str(success_csv)
                ctx.manifest["payload"]["inserted_record_ids"] = _ids_from_success_csv(success_csv)
        ctx.update_phase("post_finalize", status="complete", duration_seconds=0.0)
        ctx.save()
        return wrapper_exit
    finally:
        ctx.release_lock()


def _fetch_bulk_results(target_org: str, job_id: str, out_path: Path) -> bool:
    """Codex-R2-P1-02: sf `data bulk results` returns `successFilePath`,
    not `filesWritten`. Read the actual installed-CLI shape.
    """
    cmd = ["sf", "data", "bulk", "results", "--job-id", job_id,
           "--target-org", target_org, "--json"]
    code, stdout, _ = c.run_sf_subprocess(cmd, timeout_seconds=120, cwd=str(out_path.parent))
    if code != 0:
        return False
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return False
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict):
        return False
    success_path = result.get("successFilePath") or result.get("success_file_path")
    if not isinstance(success_path, str) or not success_path:
        return False
    try:
        src = Path(success_path)
        if not src.is_absolute():
            src = out_path.parent / src
        if src.is_symlink() or not src.resolve().is_relative_to(out_path.parent.resolve()):
            return False
        if src.is_file():
            out_path.write_bytes(src.read_bytes())
            return True
    except OSError:
        pass
    return False


def _ids_from_success_csv(csv_path: Path) -> list[str]:
    """Extract `id` column from bulk-success CSV."""
    try:
        with csv_path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return [row.get("sf__Id") or row.get("Id") or row.get("id") for row in reader
                    if (row.get("sf__Id") or row.get("Id") or row.get("id"))]
    except OSError:
        return []


def _csv_row_count(path: Path) -> int:
    try:
        with path.open(encoding="utf-8") as f:
            return sum(1 for _ in csv.DictReader(f))
    except OSError:
        return 0
