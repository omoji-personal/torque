"""jsc deploy abort — sf project deploy cancel for an in-flight deploy.

Phase I.4-extended-2 (v7.17.0). Abort cancels a still-running deploy by id.
Operation_type is `revert` semantically (abort UNDOES a partial deploy that
hasn't fully committed). Captures the deploy's last-known state for forensics.
"""

from __future__ import annotations

import argparse
import json
import time

from . import _common as c


def run(args: argparse.Namespace) -> int:
    job_id = args.job_id
    wait_min = getattr(args, "wait", 33)
    wrapper_command = f"jsc deploy abort -o {args.target_org} --job-id {job_id}"
    ctx = c.WrapperContext(
        operation_type="revert",  # abort is itself a revert action
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
        ctx.manifest["payload"] = {
            "deploy_id": job_id,
            "deploy_mode": "abort",
            "before_status": _query_status(args.target_org, job_id),
            "after_status": None,
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
        cmd = ["sf", "project", "deploy", "cancel",
               "--target-org", args.target_org,
               "--job-id", job_id,
               "--wait", str(wait_min), "--json"]
        exit_code, stdout, stderr = c.run_sf_subprocess(cmd, timeout_seconds=timeout_s)
        duration = round(time.monotonic() - t0, 2)
        (ctx.snap_dir / "underlying-result.json").write_text(stdout, encoding="utf-8")
        sf_json = c.parse_sf_json_safely(stdout)
        snap_status, wrapper_exit = c.classify_deploy_status(sf_json, exit_code)
        ctx.manifest["snapshot_status"] = snap_status
        ctx.manifest["payload"]["after_status"] = snap_status
        ctx.update_phase("underlying_command",
            status=snap_status, exit_code=exit_code, duration_seconds=duration,
            raw_artifact_paths=[str(ctx.snap_dir / "underlying-result.json")])
        ctx.update_phase("post_finalize", status="complete", duration_seconds=0.0)
        ctx.save()
        return wrapper_exit
    finally:
        ctx.release_lock()


def _query_status(target_org: str, deploy_id: str) -> str:
    cmd = ["sf", "project", "deploy", "report",
           "--target-org", target_org,
           "--job-id", deploy_id, "--json"]
    code, stdout, _ = c.run_sf_subprocess(cmd, timeout_seconds=30)
    if code != 0:
        return "unknown"
    try:
        return json.loads(stdout).get("result", {}).get("status", "unknown")
    except json.JSONDecodeError:
        return "unknown"
