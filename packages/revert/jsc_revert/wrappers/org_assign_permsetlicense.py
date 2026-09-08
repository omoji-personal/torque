"""jsc org assign permsetlicense — sf org assign permsetlicense with PSL-id capture.

Phase I.4-extended-2 (v7.17.0). PermissionSetLicenseAssignment is automatic_revertible:
revert is `DELETE FROM PermissionSetLicenseAssign WHERE Id IN (<captured_psl_ids>)`.
"""

from __future__ import annotations

import argparse
import json
import time

from . import _common as c


def run(args: argparse.Namespace) -> int:
    license_name = args.license_name
    # Codex-R1-P2-02: `sf org assign permsetlicense` flag is --on-behalf-of (-b)
    on_behalf_of = getattr(args, "on_behalf_of", None) or getattr(args, "on_user", None) or ""
    wrapper_command = (
        f"jsc org assign permsetlicense -o {args.target_org} --name {license_name}"
        + (f" --on-behalf-of {on_behalf_of}" if on_behalf_of else "")
    )
    ctx = c.WrapperContext(
        operation_type="org_assign_permsetlicense",
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
            "license_name": license_name,
            "on_behalf_of": on_behalf_of,
            "created_psl_ids": [],
        }
        ctx.set_revert_capabilities()
        ctx.update_phase("pre_snapshot", status="complete",
            duration_seconds=round(time.monotonic() - t0, 2))
        ctx.save()

        t0 = time.monotonic()
        cmd = ["sf", "org", "assign", "permsetlicense",
               "--target-org", args.target_org,
               "--name", license_name, "--json"]
        if on_behalf_of:
            cmd.extend(["--on-behalf-of", on_behalf_of])
        exit_code, stdout, stderr = c.run_sf_subprocess(cmd, timeout_seconds=60)
        duration = round(time.monotonic() - t0, 2)
        (ctx.snap_dir / "underlying-result.json").write_text(stdout)

        try:
            data = json.loads(stdout)
            results = data.get("result", {}).get("successes", []) or data.get("result", []) or []
            if isinstance(results, list):
                ids = [r.get("value", {}).get("id") if isinstance(r, dict) and "value" in r
                       else r.get("id") if isinstance(r, dict) else None for r in results]
                ctx.manifest["payload"]["created_psl_ids"] = [i for i in ids if i]
        except json.JSONDecodeError:
            pass

        snapshot_status = "complete" if exit_code == 0 else "failed"
        ctx.manifest["snapshot_status"] = snapshot_status
        ctx.update_phase("underlying_command",
            status=snapshot_status, exit_code=exit_code, duration_seconds=duration,
            raw_artifact_paths=[str(ctx.snap_dir / "underlying-result.json")])
        ctx.update_phase("post_finalize", status="complete", duration_seconds=0.0)
        ctx.save()
        return c.EXIT_SUCCESS if exit_code == 0 else c.EXIT_UNDERLYING_FAILED
    finally:
        ctx.release_lock()
