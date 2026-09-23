"""jsc data upsert wrapper — emulated single-record upsert via update + create.

Phase I.4-extended-2 (v7.17.0).

Codex-R1-P1-03 closure: `sf data upsert record` is NOT a real sf CLI command.
The supported single-record commands are `sf data update record` (existing rows
by Id) and `sf data create record` (new rows). This wrapper emulates a
single-record upsert with two CLI calls:

  1. Pre-query by external-id to detect existence.
  2. If FOUND → `sf data update record --record-id <id>`.
     If NOT_FOUND → `sf data create record`.
     If pre-query ERROR + production target → fail-closed.

Codex-R1-P1-09 closure: tagged pre-query result (FOUND / NOT_FOUND / ERROR);
ERROR aborts in production rather than silently treating as insert.

Revertibility classification: REVERTIBLE_FALSE in v7.17.0 because the planner
does not yet split upsert revert into the update- vs delete-path. The wrapper
captures both the before_row (for update-path revert) and the after_record_id
(for delete-path revert) so operators can revert manually per
revert_capabilities._forensic_recovery_path.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from . import _common as c


# Pre-query result enum
PRE_FOUND = "found"
PRE_NOT_FOUND = "not_found"
PRE_ERROR = "error"


def run(args: argparse.Namespace) -> int:
    wrapper_command = (
        f"jsc data upsert -o {args.target_org} --sobject {args.sobject} "
        f"--external-id {args.external_id} --values '{args.values}'"
    )
    ctx = c.WrapperContext(
        operation_type="data_record_upsert",
        target_org=args.target_org,
        wrapper_command=wrapper_command,
    )
    rc = ctx.resolve_org()
    if rc: return rc
    rc = ctx.acquire_org_lock()
    if rc: return rc

    try:
        ctx.init_snapshot_dir()

        # ── Pre-snapshot: parse external-id, query for existing row ──────────
        t0 = time.monotonic()
        external_id_field, external_id_value = _parse_external_id(args.external_id, args.values)
        pre_result, before_row, before_id = _query_by_external_id_tagged(
            args.target_org, args.sobject, external_id_field, external_id_value
        )

        ctx.manifest["payload"] = {
            "object_api_name": args.sobject,
            "external_id_field": external_id_field,
            "external_id_value": external_id_value,
            "pre_query_result": pre_result,
            "before_row": before_row,
            "before_record_id": before_id,
            "after_row": None,
            "after_record_id": None,
            "upsert_mode": None,  # set after we know which branch fired
            "fields_captured": list(before_row.keys()) if before_row else [],
        }
        ctx.set_revert_capabilities()
        ctx.update_phase("pre_snapshot",
            status="failed" if pre_result == PRE_ERROR else "complete",
            duration_seconds=round(time.monotonic() - t0, 2),
        )
        ctx.save()

        # Codex-R1-P1-09: pre-query ERROR + production target → fail-closed.
        # Sandbox: warn + proceed (operator can verify after).
        if pre_result == PRE_ERROR:
            if ctx.org.is_production:
                print(
                    f"error: pre-query for {args.sobject}.{external_id_field}='{external_id_value}' "
                    f"failed against production org {ctx.org.alias!r}; refusing to upsert without "
                    f"before-state capture. Re-run after fixing the query "
                    f"(check field name, FLS, network).",
                    file=sys.stderr,
                )
                return c.EXIT_PRESNAP_FAILED_PROD
            else:
                print(
                    f"warning: pre-query failed on sandbox org {ctx.org.alias!r}; "
                    f"proceeding without before-state capture (sandbox tolerance).",
                    file=sys.stderr,
                )

        # ── Underlying: branch on pre-query result ──────────────────────────
        t0 = time.monotonic()
        values_pairs = _parse_values(args.values)
        if pre_result == PRE_FOUND:
            upsert_mode = "update"
            cmd = ["sf", "data", "update", "record",
                   "--target-org", args.target_org,
                   "--sobject", args.sobject,
                   "--record-id", before_id,
                   "--values", args.values, "--json"]
        else:
            # NOT_FOUND or (sandbox-tolerated) ERROR → create
            upsert_mode = "insert"
            cmd = ["sf", "data", "create", "record",
                   "--target-org", args.target_org,
                   "--sobject", args.sobject,
                   "--values", args.values, "--json"]
        ctx.manifest["payload"]["upsert_mode"] = upsert_mode

        exit_code, stdout, stderr = c.run_sf_subprocess(cmd, timeout_seconds=60)
        duration = round(time.monotonic() - t0, 2)
        (ctx.snap_dir / "underlying-result.json").write_text(stdout, encoding="utf-8")

        snapshot_status = "complete" if exit_code == 0 else "failed"
        ctx.manifest["snapshot_status"] = snapshot_status
        ctx.update_phase("underlying_command",
            status=snapshot_status, exit_code=exit_code, duration_seconds=duration,
            raw_artifact_paths=[str(ctx.snap_dir / "underlying-result.json")],
        )

        # ── Post-finalize: extract new/updated id; re-query state ────────────
        t0 = time.monotonic()
        after_id = before_id if upsert_mode == "update" else _extract_created_id(stdout)
        after_row = None
        if after_id:
            after_row = _query_record_by_id(args.target_org, args.sobject, after_id)
        ctx.manifest["payload"]["after_row"] = after_row
        ctx.manifest["payload"]["after_record_id"] = after_id
        ctx.update_phase("post_finalize",
            status="complete" if after_row is not None else "failed",
            duration_seconds=round(time.monotonic() - t0, 2),
        )
        ctx.save()

        return c.EXIT_SUCCESS if exit_code == 0 else c.EXIT_UNDERLYING_FAILED

    finally:
        ctx.release_lock()


def _parse_external_id(ext_id_spec: str, values: str) -> tuple[str | None, str | None]:
    """Parse external id field name from --external-id flag + value from --values.

    --external-id is either `FieldName` or `FieldName=Value`. If just FieldName,
    the value is parsed from --values.
    """
    if "=" in ext_id_spec:
        field, value = ext_id_spec.split("=", 1)
        return field.strip(), value.strip()
    field = ext_id_spec.strip()
    for pair in _parse_values(values):
        k, v = pair
        if k == field:
            return field, v
    return field, None


def _parse_values(values: str) -> list[tuple[str, str]]:
    """Parse space-separated key=value pairs from --values."""
    out: list[tuple[str, str]] = []
    for pair in (values or "").split():
        if "=" in pair:
            k, v = pair.split("=", 1)
            out.append((k.strip(), v.strip()))
    return out


def _query_by_external_id_tagged(
    target_org: str, sobject: str, field: str | None, value: str | None,
) -> tuple[str, dict | None, str | None]:
    """Tagged pre-query (Codex-R1-P1-09).

    Returns (pre_result_tag, row_dict | None, record_id | None).
    pre_result_tag ∈ {PRE_FOUND, PRE_NOT_FOUND, PRE_ERROR}.

    PRE_ERROR is returned for: missing field/value, sf CLI non-zero exit,
    or malformed JSON. PRE_NOT_FOUND is returned only for clean zero-row results.
    """
    if not field or value is None:
        return PRE_ERROR, None, None
    safe_value = value.replace(chr(39), chr(39) + chr(39))
    where = f"{field}='{safe_value}'"
    cmd = ["sf", "data", "query",
           "--target-org", target_org,
           "--query", f"SELECT FIELDS(STANDARD) FROM {sobject} WHERE {where} LIMIT 1",
           "--json"]
    code, stdout, stderr = c.run_sf_subprocess(cmd, timeout_seconds=30)
    if code != 0:
        return PRE_ERROR, None, None
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return PRE_ERROR, None, None
    records = data.get("result", {}).get("records", [])
    if not records:
        return PRE_NOT_FOUND, None, None
    row = records[0]
    rid = row.get("Id")
    if not rid:
        return PRE_ERROR, None, None
    return PRE_FOUND, row, rid


def _query_record_by_id(target_org: str, sobject: str, record_id: str) -> dict | None:
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


def _extract_created_id(stdout: str) -> str | None:
    """`sf data create record --json` returns {"result": {"id": "..."}} on success."""
    try:
        data = json.loads(stdout)
        return data.get("result", {}).get("id")
    except json.JSONDecodeError:
        return None
