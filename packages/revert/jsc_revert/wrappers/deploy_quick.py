"""jsc deploy quick wrapper — sf project deploy quick (validation-deploy promotion).

Phase I.4-extended-2 (v7.17.0).

Codex-R1-P1-07 closure: deploy-quick previously claimed `deploy_metadata`
revertability (and therefore inherited the metadata-before redeploy plan), but
it did NOT capture `metadata-before/` bytes — only a 50-element component list.
For v7.17.0, the operation_type is `deploy_quick_promote` (a distinct op_type)
and the revertibility is REVERTIBLE_FALSE. Operators are directed to run
`jsc deploy --dry-run` against the validation job's component manifest BEFORE
promotion to capture a snapshot with metadata-before bytes; revert then targets
that earlier snapshot.

v7.17.1 may add `sf project retrieve start --metadata` pre-promotion to capture
metadata-before for the validation job's component list, at which point this
op_type can be promoted back to REVERTIBLE_TRUE.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from . import _common as c


# Distinct op_type so revert_capabilities classifies as False
QUICK_DEPLOY_OP_TYPE = "deploy_quick_promote"


def run(args: argparse.Namespace) -> int:
    job_id = args.job_id
    wait_min = getattr(args, "wait", 33)
    wrapper_command = f"jsc deploy quick -o {args.target_org} --job-id {job_id}"
    ctx = c.WrapperContext(
        operation_type=QUICK_DEPLOY_OP_TYPE,
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
        # Pre: query the validation result for the job id to extract its component list
        components = _query_deploy_components(args.target_org, job_id)
        ctx.manifest["payload"] = {
            "deploy_id": None,  # set after promotion
            "promoting_validation_id": job_id,
            "expected_component_count": len(components),
            "expected_components": components,  # no cap — forensic record
            "deploy_mode": "quick_promote",
        }
        ctx.set_revert_capabilities()
        ctx.update_phase("pre_snapshot", status="complete",
            duration_seconds=round(time.monotonic() - t0, 2))
        ctx.save()

        t0 = time.monotonic()
        timeout_s = wait_min * 60 + 60
        rc = c.assert_lock_safe_or_opt_in(timeout_s)
        if rc:
            return rc
        cmd = ["sf", "project", "deploy", "quick",
               "--target-org", args.target_org,
               "--job-id", job_id,
               "--wait", str(wait_min), "--json"]
        exit_code, stdout, stderr = c.run_sf_subprocess(cmd, timeout_seconds=timeout_s)
        duration = round(time.monotonic() - t0, 2)
        (ctx.snap_dir / "underlying-result.json").write_text(stdout, encoding="utf-8")
        sf_json = c.parse_sf_json_safely(stdout)
        if sf_json:
            ctx.manifest["payload"]["deploy_id"] = sf_json.get("result", {}).get("id")
        snap_status, wrapper_exit = c.classify_deploy_status(sf_json, exit_code)
        ctx.manifest["snapshot_status"] = snap_status
        ctx.update_phase("underlying_command",
            status=snap_status, exit_code=exit_code, duration_seconds=duration,
            raw_artifact_paths=[str(ctx.snap_dir / "underlying-result.json")])
        # Codex-R1-P1-02: auto-enqueue if pending
        deploy_id = ctx.manifest["payload"].get("deploy_id")
        if snap_status == "pending_finalize_required" and deploy_id:
            c.auto_enqueue_if_pending(ctx, snap_status, deploy_id,
                ["sf", "project", "deploy", "report", "--target-org", args.target_org,
                 "--job-id", deploy_id, "--json"])
        ctx.update_phase("post_finalize", status="complete", duration_seconds=0.0)
        ctx.save()
        return wrapper_exit
    finally:
        ctx.release_lock()


def _query_deploy_components(target_org: str, deploy_id: str) -> list[dict]:
    cmd = ["sf", "project", "deploy", "report",
           "--target-org", target_org,
           "--job-id", deploy_id, "--json"]
    code, stdout, _ = c.run_sf_subprocess(cmd, timeout_seconds=60)
    if code != 0:
        return []
    try:
        result = json.loads(stdout).get("result", {})
        return result.get("details", {}).get("componentSuccesses", []) or []
    except json.JSONDecodeError:
        return []
