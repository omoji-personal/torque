"""jsc org assign permsetlicense — sf org assign permsetlicense with PSL-id capture.

Assignment IDs support reviewed manual removal from PermissionSetLicenseAssign.
Automatic recovery is not implemented.
"""

from __future__ import annotations

import argparse
import shlex
import sys
import time

from . import _common as c
from . import _assignment_capture as capture
from .org_assign_permset import _assignment_result


def run(args: argparse.Namespace) -> int:
    def values(value):
        return value if isinstance(value, list) else [value] if value else []

    license_name = values(args.license_name)
    on_behalf_of = values(getattr(args, "on_behalf_of", None)) + values(getattr(args, "on_user", None))
    options = [token for name in license_name for token in ("--name", name)]
    options += [token for user in on_behalf_of for token in ("--on-behalf-of", user)]
    wrapper_command = shlex.join(["jsc", "org", "assign", "permsetlicense", "-o", args.target_org] + options)
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
        query = capture.assignment_query(ctx.org, license_name, on_behalf_of, license=True)
        before = capture.read_ids(args.target_org, query, '2LA')
        ctx.manifest['payload']['assignment_before_ids'] = list(before.values()) if before is not None else None
        ctx.update_phase("pre_snapshot", status="complete",
            duration_seconds=round(time.monotonic() - t0, 2))
        ctx.save()

        t0 = time.monotonic()
        cmd = ["sf", "org", "assign", "permsetlicense",
               "--target-org", args.target_org, "--json"] + options
        exit_code, stdout, stderr = c.run_sf_subprocess(cmd, timeout_seconds=60)
        duration = round(time.monotonic() - t0, 2)
        (ctx.snap_dir / "underlying-result.json").write_text(stdout, encoding="utf-8")

        evidence = _assignment_result(stdout, kind='psl', prefix='2LA')
        ctx.manifest['payload'].update(evidence)
        after = capture.read_ids(args.target_org, query, '2LA')
        capture.record_difference(ctx.manifest['payload'], before, after, 'psl')
        successes = evidence['assignment_success_count'] or 0
        failures = evidence['assignment_failure_count'] or 0
        if successes and (exit_code != 0 or failures):
            snapshot_status, wrapper_exit = 'applied_partial', c.EXIT_SUCCEEDED_PARTIAL
        elif exit_code != 0 or failures:
            snapshot_status, wrapper_exit = 'failed', c.EXIT_UNDERLYING_FAILED
        else:
            snapshot_status, wrapper_exit = 'complete', c.EXIT_SUCCESS
        captured = ctx.manifest['payload']['psl_id_capture_status'] == 'captured'
        if not captured:
            print('warning: assignment ID capture is incomplete; identify the exact newly created assignment before manual recovery.', file=sys.stderr)
        ctx.set_revert_capabilities()
        ctx.manifest["snapshot_status"] = snapshot_status
        ctx.update_phase("underlying_command",
            status=snapshot_status, exit_code=exit_code, duration_seconds=duration,
            raw_artifact_paths=[str(ctx.snap_dir / "underlying-result.json")])
        ctx.update_phase("post_finalize", status="complete" if captured else 'partial', duration_seconds=0.0)
        ctx.save()
        return wrapper_exit
    finally:
        ctx.release_lock()
