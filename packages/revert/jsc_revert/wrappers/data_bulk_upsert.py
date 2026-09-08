"""jsc data upsert bulk wrapper — sf data upsert bulk with external-id-keyed pre-snapshot.

Phase I.4-extended-2 (v7.17.0). Bulk upsert: best-effort revertibility. Insert
path → delete-by-id; update path → reapply before_csv. The wrapper queries
existing rows by external-id BEFORE the bulk, stores into before.csv.
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


def run(args: argparse.Namespace) -> int:
    wrapper_command = (
        f"jsc data upsert bulk -o {args.target_org} --sobject {args.sobject} "
        f"--external-id {args.external_id} --file {args.file}"
    )
    ctx = c.WrapperContext(
        operation_type="data_bulk_upsert",
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

        ext_values, fields = _parse_bulk_csv_ext_id(input_path, args.external_id)
        before_csv_path = ctx.snap_dir / "before.csv"
        # Codex-R2-P1-03: tagged pre-query — distinguish clean-no-match from
        # query error so production fails closed on error.
        pre_status, before_count = _query_by_ext_ids_tagged(
            args.target_org, args.sobject, args.external_id, ext_values, fields, before_csv_path
        )
        ctx.manifest["payload"] = {
            "object_api_name": args.sobject,
            "external_id_field": args.external_id,
            "input_file": str(input_path),
            "input_row_count": len(ext_values),
            "before_csv": str(before_csv_path) if before_count else None,
            "before_record_count": before_count,
            "pre_query_status": pre_status,  # "ok" | "no_match" | "error"
            "upsert_mode": "mixed",  # insert+update combined
            "fields_captured": fields,
            "bulk_job_id": None,
        }
        ctx.set_revert_capabilities()
        ctx.update_phase("pre_snapshot",
            status="failed" if pre_status == "error" else "complete",
            duration_seconds=round(time.monotonic() - t0, 2))
        ctx.save()

        if pre_status == "error" and ctx.org.is_production:
            print(
                f"error: bulk upsert pre-query failed on production org "
                f"{ctx.org.alias!r}; refusing to run without complete before-state. "
                f"Re-run after fixing the query (check field/FLS/network).",
                file=sys.stderr,
            )
            return c.EXIT_PRESNAP_FAILED_PROD

        t0 = time.monotonic()
        wait_min = getattr(args, "wait", 30)
        timeout_s = wait_min * 60 + 60
        rc = c.assert_lock_safe_or_opt_in(timeout_s)
        if rc:
            return rc
        cmd = ["sf", "data", "upsert", "bulk",
               "--target-org", args.target_org,
               "--sobject", args.sobject,
               "--external-id", args.external_id,
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
        snap_status, wrapper_exit, job_id = c.capture_bulk_outcome(ctx, sf_json, exit_code)
        ctx.manifest["payload"]["bulk_job_id"] = job_id
        ctx.manifest["snapshot_status"] = snap_status
        ctx.update_phase("underlying_command",
            status=snap_status, exit_code=exit_code, duration_seconds=duration,
            raw_artifact_paths=[str(ctx.snap_dir / "underlying-result.json")])
        if snap_status == "pending_finalize_required" and job_id:
            c.auto_enqueue_if_pending(ctx, snap_status, job_id,
                ["sf", "data", "upsert", "resume", "--target-org", args.target_org,
                 "--job-id", job_id, "--json"])
        # Codex-R1-P1-06: fetch inserted IDs from bulk results (best-effort)
        if snap_status in ("complete", "applied_partial") and job_id:
            success_csv = ctx.snap_dir / "after_success.csv"
            _fetch_bulk_results(args.target_org, job_id, success_csv)
            if success_csv.exists():
                ctx.manifest["payload"]["after_success_csv"] = str(success_csv)
        ctx.update_phase("post_finalize", status="complete", duration_seconds=0.0)
        ctx.save()
        return wrapper_exit
    finally:
        ctx.release_lock()


def _fetch_bulk_results(target_org: str, job_id: str, out_path: Path) -> bool:
    """Codex-R1-P1-06 + Codex-R2-P1-02: fetch sf data bulk results.

    Installed sf CLI returns `result.successFilePath` and `result.failedFilePath`
    on JobComplete (not `result.filesWritten` as the v7.17.0-initial draft assumed).
    Returns True on success, False on any failure (best-effort).
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


def _parse_bulk_csv_ext_id(path: Path, ext_field: str) -> tuple[list[str], list[str]]:
    text = path.read_text()
    reader = csv.DictReader(io.StringIO(text))
    fields = list(reader.fieldnames or [])
    vals: list[str] = []
    for row in reader:
        v = row.get(ext_field)
        if v:
            vals.append(v)
    return vals, fields


PAGE_SIZE = 200


def _query_by_ext_ids_tagged(target_org: str, sobject: str, ext_field: str,
                              ext_values: list[str], fields: list[str], out_path: Path
                              ) -> tuple[str, int]:
    """Codex-R2-P1-03: tagged pre-query for bulk upsert.

    Returns (status, count) where status is one of:
      "ok"       — query succeeded; matched existing rows written to out_path
      "no_match" — query succeeded; zero matching rows (legitimate all-insert)
      "error"    — query failed (CLI non-zero exit or malformed JSON) on ANY chunk
    """
    if not ext_values or not fields:
        return ("no_match", 0)
    field_list = ",".join(fields)
    all_records: list[dict] = []
    for chunk_start in range(0, len(ext_values), PAGE_SIZE):
        chunk = ext_values[chunk_start:chunk_start + PAGE_SIZE]
        val_list = ",".join(f"'{v.replace(chr(39), chr(39)+chr(39))}'" for v in chunk)
        query = f"SELECT {field_list} FROM {sobject} WHERE {ext_field} IN ({val_list})"
        cmd = ["sf", "data", "query", "--target-org", target_org,
               "--query", query, "--json"]
        code, stdout, _ = c.run_sf_subprocess(cmd, timeout_seconds=180)
        if code != 0:
            return ("error", 0)
        try:
            chunk_records = json.loads(stdout).get("result", {}).get("records", [])
        except json.JSONDecodeError:
            return ("error", 0)
        all_records.extend(chunk_records)
    if not all_records:
        return ("no_match", 0)
    with out_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in all_records:
            w.writerow({k: r.get(k, "") for k in fields})
    return ("ok", len(all_records))
