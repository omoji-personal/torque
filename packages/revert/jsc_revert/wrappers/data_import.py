"""jsc data import wrapper — sf data import tree with sObject-tree pre-snapshot.

Phase I.4-extended-2 (v7.17.0). Tree imports create N records across multiple
sObjects. Best-effort revertibility: collect all inserted record IDs from the
sf JSON output, store as before_ids for delete-by-id revert. FK chains may
block the revert if children reference parents that were created in the same
import.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import _common as c


def run(args: argparse.Namespace) -> int:
    wrapper_command = f"jsc data import -o {args.target_org} --plan {args.plan}"
    ctx = c.WrapperContext(
        operation_type="data_record_import",
        target_org=args.target_org,
        wrapper_command=wrapper_command,
    )
    rc = ctx.resolve_org()
    if rc: return rc
    rc = ctx.acquire_org_lock()
    if rc: return rc

    try:
        ctx.init_snapshot_dir()

        # Pre: capture plan contents for forensics (no live query — import is creates)
        t0 = time.monotonic()
        plan_path = Path(args.plan)
        plan_text = plan_path.read_text(encoding="utf-8") if plan_path.exists() else ""
        (ctx.snap_dir / "import_plan.json").write_text(plan_text, encoding="utf-8")
        ctx.manifest["payload"] = {
            "plan_path": str(plan_path),
            "plan_size_bytes": len(plan_text),
            "inserted_record_ids": [],
            "inserted_by_sobject": {},
        }
        ctx.set_revert_capabilities()
        ctx.update_phase("pre_snapshot",
            status="complete" if plan_text else "failed",
            duration_seconds=round(time.monotonic() - t0, 2),
        )
        ctx.save()

        if not plan_text and ctx.org.is_production:
            return c.EXIT_PRESNAP_FAILED_PROD

        # ── Underlying ────────────────────────────────────────────────────
        t0 = time.monotonic()
        cmd = ["sf", "data", "import", "tree",
               "--target-org", args.target_org,
               "--plan", str(plan_path), "--json"]
        exit_code, stdout, stderr = c.run_sf_subprocess(cmd, timeout_seconds=600)
        duration = round(time.monotonic() - t0, 2)
        (ctx.snap_dir / "underlying-result.json").write_text(stdout, encoding="utf-8")

        snapshot_status = "complete" if exit_code == 0 else "failed"
        ctx.manifest["snapshot_status"] = snapshot_status
        ctx.update_phase("underlying_command",
            status=snapshot_status, exit_code=exit_code, duration_seconds=duration,
            raw_artifact_paths=[str(ctx.snap_dir / "underlying-result.json"),
                                str(ctx.snap_dir / "import_plan.json")],
        )

        # Post: parse inserted record IDs from sf output
        t0 = time.monotonic()
        ids, by_sobject = _extract_inserted_ids(stdout)
        ctx.manifest["payload"]["inserted_record_ids"] = ids
        ctx.manifest["payload"]["inserted_by_sobject"] = by_sobject
        ctx.update_phase("post_finalize",
            status="complete" if ids or exit_code == 0 else "failed",
            duration_seconds=round(time.monotonic() - t0, 2),
        )
        ctx.save()

        return c.EXIT_SUCCESS if exit_code == 0 else c.EXIT_UNDERLYING_FAILED

    finally:
        ctx.release_lock()


def _extract_inserted_ids(stdout: str) -> tuple[list[str], dict[str, list[str]]]:
    """Parse `sf data import tree --json` output. Returns (all_ids, by_sobject)."""
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return [], {}
    result = data.get("result", [])
    if not isinstance(result, list):
        return [], {}
    all_ids: list[str] = []
    by_sobject: dict[str, list[str]] = {}
    for entry in result:
        if not isinstance(entry, dict):
            continue
        rid = entry.get("id")
        sobj = entry.get("refId") or entry.get("type") or "unknown"
        if rid:
            all_ids.append(rid)
            by_sobject.setdefault(sobj, []).append(rid)
    return all_ids, by_sobject
