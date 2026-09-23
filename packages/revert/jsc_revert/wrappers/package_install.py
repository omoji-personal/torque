"""jsc package install wrapper — sf package install with version capture.

Phase I.4-extended-2 (v7.17.0). Package install is NON-REVERTIBLE per
revert_capabilities — uninstall is a separate operation that may not be
available for managed packages. The wrapper captures package version
metadata for forensic record only.
"""

from __future__ import annotations

import argparse
import json
import time

from . import _common as c


def run(args: argparse.Namespace) -> int:
    package_id = args.package
    wait_min = getattr(args, "wait", 30)
    wrapper_command = f"jsc package install -o {args.target_org} --package {package_id}"
    ctx = c.WrapperContext(
        operation_type="package_install",
        target_org=args.target_org,
        wrapper_command=wrapper_command,
    )
    rc = ctx.resolve_org()
    if rc: return rc
    rc = ctx.acquire_org_lock()
    if rc: return rc

    try:
        ctx.init_snapshot_dir()

        # Pre: query installed package list BEFORE install for diff
        t0 = time.monotonic()
        before_packages = _list_installed_packages(args.target_org)
        ctx.manifest["payload"] = {
            "package_id": package_id,
            "before_installed_count": len(before_packages),
            "before_installed_packages": before_packages[:20],  # cap to keep manifest small
            "after_installed_count": 0,
            "after_installed_packages": [],
            "newly_installed": [],
        }
        ctx.set_revert_capabilities()  # always False for package_install
        ctx.update_phase("pre_snapshot", status="complete",
            duration_seconds=round(time.monotonic() - t0, 2))
        ctx.save()

        t0 = time.monotonic()
        timeout_s = wait_min * 60 + 60
        rc = c.assert_lock_safe_or_opt_in(timeout_s)
        if rc:
            return rc
        cmd = ["sf", "package", "install",
               "--target-org", args.target_org,
               "--package", package_id,
               "--wait", str(wait_min),
               "--no-prompt", "--json"]
        exit_code, stdout, stderr = c.run_sf_subprocess(cmd, timeout_seconds=timeout_s)
        duration = round(time.monotonic() - t0, 2)
        (ctx.snap_dir / "underlying-result.json").write_text(stdout, encoding="utf-8")

        # Codex-R2-P1-01: parse request status from JSON; IN_PROGRESS/UNKNOWN → pending → enqueue
        request_id = None
        request_status = None
        try:
            jdata = json.loads(stdout)
            request_id = jdata.get("result", {}).get("Id") or jdata.get("result", {}).get("id")
            request_status = jdata.get("result", {}).get("Status") or jdata.get("result", {}).get("status")
        except json.JSONDecodeError:
            pass

        if exit_code == 0 and request_status == "SUCCESS":
            snapshot_status = "complete"
        elif request_status in ("IN_PROGRESS", "UNKNOWN", "QUEUED"):
            snapshot_status = "pending_finalize_required"
        elif exit_code == 0:
            snapshot_status = "complete"
        else:
            snapshot_status = "failed"
        ctx.manifest["snapshot_status"] = snapshot_status
        ctx.manifest["payload"]["install_request_id"] = request_id
        ctx.manifest["payload"]["install_request_status"] = request_status
        ctx.update_phase("underlying_command",
            status=snapshot_status, exit_code=exit_code, duration_seconds=duration,
            raw_artifact_paths=[str(ctx.snap_dir / "underlying-result.json")])
        if snapshot_status == "pending_finalize_required" and request_id:
            c.auto_enqueue_if_pending(ctx, snapshot_status, request_id,
                ["sf", "package", "install", "report",
                 "--target-org", args.target_org,
                 "--request-id", request_id, "--json"])

        # Post: re-query installed packages, diff
        t0 = time.monotonic()
        after_packages = _list_installed_packages(args.target_org)
        before_ids = {p.get("SubscriberPackageId") or p.get("Id") for p in before_packages}
        newly = [p for p in after_packages if (p.get("SubscriberPackageId") or p.get("Id")) not in before_ids]
        ctx.manifest["payload"]["after_installed_count"] = len(after_packages)
        ctx.manifest["payload"]["after_installed_packages"] = after_packages[:20]
        ctx.manifest["payload"]["newly_installed"] = newly[:10]
        ctx.update_phase("post_finalize", status="complete",
            duration_seconds=round(time.monotonic() - t0, 2))
        ctx.save()
        return c.EXIT_SUCCESS if exit_code == 0 else c.EXIT_UNDERLYING_FAILED
    finally:
        ctx.release_lock()


def _list_installed_packages(target_org: str) -> list[dict]:
    cmd = ["sf", "package", "installed", "list",
           "--target-org", target_org, "--json"]
    code, stdout, _ = c.run_sf_subprocess(cmd, timeout_seconds=60)
    if code != 0:
        return []
    try:
        return json.loads(stdout).get("result", [])
    except json.JSONDecodeError:
        return []
