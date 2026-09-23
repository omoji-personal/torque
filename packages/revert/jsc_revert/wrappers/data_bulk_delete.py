"""jsc data delete bulk wrapper — sf data delete bulk with soft/hard tristate.

Standard/custom described fields are captured before deletion. Bulk recovery is
manual; a hard delete cannot restore original IDs, related data or hidden fields.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import time
from pathlib import Path

from . import _common as c
from .. import bundle


def run(args: argparse.Namespace) -> int:
    hard_delete = bool(getattr(args, "hard_delete", False))
    wrapper_command = (
        f"jsc data delete bulk -o {args.target_org} --sobject {args.sobject} "
        f"--file {args.file}{' --hard-delete' if hard_delete else ''}"
    )
    op_type = "data_bulk_delete"
    ctx = c.WrapperContext(
        operation_type=op_type,
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

        # Extract record IDs from CSV
        ids = _ids_from_csv(input_path)
        # Query before-state (always — even on hard delete, we want the forensic CSV)
        before_csv = ctx.snap_dir / "before.csv"
        capture = {}
        before_count = _query_all_fields(args.target_org, args.sobject, ids, before_csv, capture=capture)
        ctx.manifest["payload"] = {
            "object_api_name": args.sobject,
            "input_file": str(input_path),
            "record_ids": ids,
            "before_csv": str(before_csv) if before_count else None,
            "before_record_count": before_count,
            "before_json": str(before_csv.with_suffix(".json")) if capture.get("complete") else None,
            "field_capture": capture,
            "delete_mode": "hard" if hard_delete else "soft",
            "bulk_job_id": None,
        }
        ctx.set_revert_capabilities()
        ctx.update_phase("pre_snapshot",
            status="complete" if capture.get("complete") else "failed",
            duration_seconds=round(time.monotonic() - t0, 2))
        ctx.save()

        # Deletion must not outrun a capture that has missing rows or fields.
        if not capture.get("complete"):
            ctx.manifest["snapshot_status"] = "failed"
            ctx.save()
            print(
                f"error: bulk delete was not submitted: before-state capture is incomplete. "
                f"{capture.get('error', 'Inspect field_capture in the snapshot.')} "
                "Resolve the export problem and retry the capture before deleting records.",
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
        cmd = ["sf", "data", "delete", "bulk",
               "--target-org", args.target_org,
               "--sobject", args.sobject,
               "--file", str(input_path),
               "--line-ending", c.csv_line_ending(input_path),
               "--wait", str(wait_min),
               "--json"]
        if hard_delete:
            cmd.append("--hard-delete")
        exit_code, stdout, stderr = c.run_sf_subprocess(cmd, timeout_seconds=timeout_s)
        duration = round(time.monotonic() - t0, 2)
        (ctx.snap_dir / "underlying-result.json").write_text(stdout, encoding="utf-8")
        c.bundle.atomic_write_json(ctx.snap_dir / "underlying-command.json",
            {"command": cmd, "exit_code": exit_code, "stdout": stdout, "stderr": stderr})
        sf_json = c.parse_sf_json_safely(stdout)
        # Codex-R1-P1-04: bulk-specific classifier
        snap_status, wrapper_exit, job_id = c.capture_bulk_outcome(ctx, sf_json, exit_code)
        ctx.manifest["payload"]["bulk_job_id"] = job_id
        ctx.manifest["snapshot_status"] = snap_status
        ctx.update_phase("underlying_command",
            status=snap_status, exit_code=exit_code, duration_seconds=duration,
            raw_artifact_paths=[str(ctx.snap_dir / "underlying-result.json")])
        # Codex-R1-P1-02: auto-enqueue if pending
        if snap_status == "pending_finalize_required" and job_id:
            c.auto_enqueue_if_pending(ctx, snap_status, job_id,
                ["sf", "data", "delete", "resume", "--target-org", args.target_org,
                 "--job-id", job_id, "--json"])
        ctx.update_phase("post_finalize", status="complete", duration_seconds=0.0)
        ctx.save()
        return wrapper_exit
    finally:
        ctx.release_lock()


def _ids_from_csv(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    reader = csv.DictReader(io.StringIO(text))
    ids: list[str] = []
    for row in reader:
        rid = row.get("Id") or row.get("ID") or row.get("id")
        if rid:
            ids.append(rid)
    return ids


PAGE_SIZE = 200
FIELD_PAGE_SIZE = 100


def _query_all_fields(target_org: str, sobject: str, ids: list[str],
                     out_path: Path, *, capture: dict | None = None) -> int:
    """Export described standard/custom fields in bounded row/field chunks.

    The typed JSON companion preserves nulls and compound values exactly; CSV is
    a convenient tabular view. Capture scope is the current user's describe, not
    a claim about inaccessible fields, files, child records or automation state.
    """
    capture = capture if capture is not None else {}
    capture.update(complete=False, scope="fields returned by the current user's object describe",
                   fields_requested=[], fields_captured=[], error=None,
                   limitations=["Fields inaccessible to this user are not assessed.",
                                "Related records, files and automation state are not included.",
                                "CSV empty cells represent nulls; before.json preserves exact types."])
    def failed(reason):
        capture["error"] = reason
        return 0
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", sobject):
        return failed("Invalid object API name.")
    if not ids or any(not re.fullmatch(r"[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?", rid) for rid in ids):
        return failed("Input must contain valid Salesforce record IDs.")
    ids = list({rid[:15]: rid for rid in ids}.values())
    code, stdout, _ = c.run_sf_subprocess(
        ["sf", "sobject", "describe", "--target-org", target_org, "--sobject", sobject, "--json"], timeout_seconds=60)
    if code:
        return failed("Object describe failed; no complete field inventory is available.")
    try:
        described = json.loads(stdout)["result"]["fields"]
        if not isinstance(described, list) or not described:
            raise ValueError("missing fields")
        if any(not isinstance(field, dict) for field in described):
            raise ValueError("malformed field")
        rows = [field for field in described if not field.get("deprecatedAndHidden")]
        fields = list(dict.fromkeys(field["name"] for field in rows))
        if "Id" not in fields or any(not isinstance(field, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", field) for field in fields):
            raise ValueError("invalid field inventory")
        capture["fields_requested"] = fields
        binary = [field["name"] for field in rows if field.get("type") == "base64"]
        if binary:
            return failed("Binary field content needs a separate export before deletion: " + ", ".join(binary))
    except (ValueError, KeyError, TypeError):
        return failed("Object describe returned an invalid field inventory.")
    merged = {}
    remaining = [field for field in fields if field != "Id"]
    batches = [remaining[i:i + FIELD_PAGE_SIZE] for i in range(0, len(remaining), FIELD_PAGE_SIZE)] or [[]]
    for chunk_start in range(0, len(ids), PAGE_SIZE):
        chunk = ids[chunk_start:chunk_start + PAGE_SIZE]
        expected = {rid[:15] for rid in chunk}
        id_list = ",".join(f"'{rid}'" for rid in chunk)
        for batch in batches:
            columns = ["Id", *batch]
            query = f"SELECT {','.join(columns)} FROM {sobject} WHERE Id IN ({id_list})"
            code, stdout, _ = c.run_sf_subprocess(
                ["sf", "data", "query", "--target-org", target_org, "--query", query, "--json"], timeout_seconds=180)
            if code:
                return failed("A field/record query failed; the capture is incomplete.")
            try:
                result = json.loads(stdout)["result"]
                records = result["records"]
                if not isinstance(records, list) or result.get("done") is False:
                    raise ValueError("incomplete response")
                observed = set()
                for row in records:
                    if not isinstance(row, dict) or any(column not in row for column in columns):
                        raise ValueError("missing field")
                    rid = row["Id"]
                    if not isinstance(rid, str) or rid[:15] not in expected or rid[:15] in observed:
                        raise ValueError("unexpected record")
                    observed.add(rid[:15])
                    merged.setdefault(rid[:15], {}).update({column: row[column] for column in columns})
                if observed != expected:
                    raise ValueError("missing record")
            except (ValueError, KeyError, TypeError):
                return failed("Query response omitted or contradicted requested records/fields.")
    records = [merged[rid[:15]] for rid in ids]
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=fields)
    writer.writeheader()
    for row in records:
        writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list, bool)) else value for key, value in row.items()})
    bundle.atomic_write_json(out_path.with_suffix(".json"), {"fields": fields, "records": records}, mode=0o600)
    bundle.atomic_write_text(out_path, text.getvalue(), mode=0o600)
    capture.update(complete=True, fields_captured=fields)
    return len(records)
