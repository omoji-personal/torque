"""jsc apex run wrapper — sf apex run with snapshot.

Apex_run is automatic_revertible=False per revert_capabilities; this wrapper
captures best-effort evidence (apex source, debug log id, per-touched-object
row counts) for manual recovery only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from . import _common as c


def run(args: argparse.Namespace) -> int:
    apex_path = Path(args.apex_file)
    if not apex_path.exists():
        print(f"error: apex file not found: {apex_path}", file=sys.stderr)
        return c.EXIT_PRESNAP_FAILED_PROD
    apex_code = apex_path.read_text(encoding="utf-8")

    wrapper_command = f"jsc apex run -o {args.target_org} -f {args.apex_file}"
    if args.touches: wrapper_command += f" --touches {args.touches}"

    ctx = c.WrapperContext(
        operation_type="apex_run",
        target_org=args.target_org,
        wrapper_command=wrapper_command,
    )

    rc = ctx.resolve_org()
    if rc: return rc

    rc = ctx.acquire_org_lock()
    if rc: return rc

    try:
        ctx.init_snapshot_dir()
        touched_declared = [t.strip() for t in (args.touches or "").split(",") if t.strip()]
        ctx.manifest["payload"] = {
            "apex_input_path": str(apex_path),
            "apex_input_sha256": "sha256:" + hashlib.sha256(apex_code.encode()).hexdigest(),
            "anonymous_id": None,
            "debug_log_id": None,
            "touched_objects_declared": touched_declared,
            "touched_objects_inferred_from_log": [],
            "touch_method": "declared" if touched_declared else "unknown",
            "before_state_per_object": {},
            "after_state_per_object": {},
        }
        ctx.set_revert_capabilities()

        # ── Loud pre-run warning for non-revertible operation ─────────────
        if ctx.org.is_production and os.environ.get("JSC_REVERT_NONREVERTIBLE_AUTO_ACK") != "1":
            print(
                f"\n⚠️  Apex run against PRODUCTION org {ctx.org.alias} ({ctx.org.org_id_18})\n"
                f"   This operation is NOT automatically revertible. Snapshot will\n"
                f"   capture best-effort evidence (apex source, debug log id) for\n"
                f"   manual recovery only.\n"
                f"\n"
                f"Continue? (yes/no): ",
                file=sys.stderr, end=""
            )
            try:
                answer = input().strip().lower()
            except EOFError:
                answer = "no"
            if answer != "yes":
                print("Aborted.", file=sys.stderr)
                ctx.manifest["snapshot_status"] = "abandoned"
                ctx.save()
                return c.EXIT_PRESNAP_FAILED_PROD

        # Persist apex source for forensics
        (ctx.snap_dir / "apex_input.apex").write_text(apex_code, encoding="utf-8")

        # ── Phase 1: pre-state per declared touched objects ────────────────
        t0 = time.monotonic()
        for obj in touched_declared:
            count, max_lmd = _query_object_state(args.target_org, obj)
            ctx.manifest["payload"]["before_state_per_object"][obj] = {
                "row_count": count, "max_lastModifiedDate": max_lmd,
            }
        ctx.update_phase("pre_snapshot",
            status="complete",
            duration_seconds=round(time.monotonic() - t0, 2),
            raw_artifact_paths=[],
        )
        ctx.save()

        # ── Phase 2: invoke sf apex run ────────────────────────────────────
        t0 = time.monotonic()
        exit_code, stdout, stderr = c.run_sf_subprocess(
            ["sf", "apex", "run", "--target-org", args.target_org, "--file", str(apex_path), "--json"],
            timeout_seconds=600,
        )
        duration = round(time.monotonic() - t0, 2)
        sf_json = c.parse_sf_json_safely(stdout)

        if sf_json:
            r = sf_json.get("result", {})
            ctx.manifest["payload"]["anonymous_id"] = r.get("id")
            ctx.manifest["payload"]["debug_log_id"] = r.get("logs", "")[:64]  # truncate

        (ctx.snap_dir / "underlying-result.json").write_text(stdout, encoding="utf-8")
        snapshot_status = "complete" if exit_code == 0 else "failed"
        ctx.manifest["snapshot_status"] = snapshot_status
        ctx.update_phase("underlying_command",
            status=snapshot_status, exit_code=exit_code, duration_seconds=duration,
            raw_artifact_paths=[str(ctx.snap_dir / "underlying-result.json")],
        )

        # ── Phase 3: post-state ────────────────────────────────────────────
        t0 = time.monotonic()
        for obj in touched_declared:
            count, max_lmd = _query_object_state(args.target_org, obj)
            ctx.manifest["payload"]["after_state_per_object"][obj] = {
                "row_count": count, "max_lastModifiedDate": max_lmd,
            }
        ctx.update_phase("post_finalize",
            status="complete", duration_seconds=round(time.monotonic() - t0, 2),
        )
        ctx.save()

        return c.EXIT_SUCCESS if exit_code == 0 else c.EXIT_UNDERLYING_FAILED

    finally:
        ctx.release_lock()


def _query_object_state(target_org: str, sobject: str) -> tuple[int, str]:
    """Best-effort: get (row_count, max_lastModifiedDate) for an sobject."""
    import json as _json
    cmd = ["sf", "data", "query", "--target-org", target_org,
           "--query", f"SELECT COUNT(Id), MAX(LastModifiedDate) FROM {sobject}",
           "--json"]
    code, stdout, _ = c.run_sf_subprocess(cmd, timeout_seconds=30)
    if code != 0:
        return -1, ""
    try:
        data = _json.loads(stdout)
        records = data.get("result", {}).get("records", [])
        if records:
            r = records[0]
            return int(r.get("expr0", 0)), str(r.get("expr1", ""))
    except (KeyError, ValueError, TypeError, _json.JSONDecodeError):
        pass
    return -1, ""


import os
