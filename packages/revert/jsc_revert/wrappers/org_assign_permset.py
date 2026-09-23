"""Assign a permission set and preserve the command outcome for manual recovery.

Current Salesforce CLI successes identify usernames and permission-set names,
not assignment IDs. Capture valid PSA IDs when supplied; never invent them or
claim an automatic recovery plan.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time

from . import _common as c


_PSA_ID = re.compile(r"0Pa[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?\Z")


def _assignment_result(stdout: str) -> dict:
    """Normalize supported result shapes without trusting arbitrary IDs as PSA IDs."""
    unknown = {"created_psa_ids": [], "assignment_success_count": None,
               "assignment_failure_count": None, "psa_id_capture_status": "unrecognized_response"}
    try:
        envelope = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return unknown
    if not isinstance(envelope, dict):
        return unknown
    result = envelope.get("result")
    if isinstance(result, list):
        successes = result
        failures = []
    elif isinstance(result, dict) and isinstance(result.get("successes"), list):
        successes = result["successes"]
        failures = result.get("failures", [])
        if not isinstance(failures, list):
            return unknown
    else:
        return unknown
    ids = []
    ids_returned = 0
    unsuccessful = 0
    for row in successes:
        if not isinstance(row, dict):
            continue
        if row.get("success") is False or row.get("errors"):
            unsuccessful += 1
            continue
        value = row.get("value")
        candidate = value.get("id") if isinstance(value, dict) else None
        if candidate is None:
            candidate = row.get("id")
        if isinstance(candidate, str) and _PSA_ID.fullmatch(candidate):
            ids_returned += 1
            if candidate not in ids:
                ids.append(candidate)
    success_count = len(successes) - unsuccessful
    capture_status = "not_returned"
    if ids_returned:
        capture_status = "captured" if ids_returned == success_count else "partial"
    return {"created_psa_ids": ids, "assignment_success_count": success_count,
            "assignment_failure_count": len(failures) + unsuccessful,
            "psa_id_capture_status": capture_status}


def run(args: argparse.Namespace) -> int:
    perm_set = args.perm_set_name
    # Codex-R1-P2-02: `sf org assign permset` flag is --on-behalf-of (-b), NOT --on-user
    on_behalf_of = getattr(args, "on_behalf_of", None) or getattr(args, "on_user", None) or ""
    wrapper_command = (
        f"jsc org assign permset -o {args.target_org} --name {perm_set}"
        + (f" --on-behalf-of {on_behalf_of}" if on_behalf_of else "")
    )
    ctx = c.WrapperContext(
        operation_type="org_assign_permset",
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
            "perm_set_name": perm_set,
            "on_behalf_of": on_behalf_of,
            "created_psa_ids": [],
        }
        ctx.set_revert_capabilities()
        ctx.update_phase("pre_snapshot", status="complete",
            duration_seconds=round(time.monotonic() - t0, 2))
        ctx.save()

        t0 = time.monotonic()
        cmd = ["sf", "org", "assign", "permset",
               "--target-org", args.target_org,
               "--name", perm_set, "--json"]
        if on_behalf_of:
            cmd.extend(["--on-behalf-of", on_behalf_of])
        exit_code, stdout, stderr = c.run_sf_subprocess(cmd, timeout_seconds=60)
        duration = round(time.monotonic() - t0, 2)
        (ctx.snap_dir / "underlying-result.json").write_text(stdout, encoding="utf-8")

        evidence = _assignment_result(stdout)
        ctx.manifest["payload"].update(evidence)
        successes = evidence["assignment_success_count"] or 0
        failures = evidence["assignment_failure_count"] or 0
        if successes and (exit_code != 0 or failures):
            snapshot_status, wrapper_exit = "applied_partial", c.EXIT_SUCCEEDED_PARTIAL
        elif exit_code != 0 or failures:
            snapshot_status, wrapper_exit = "failed", c.EXIT_UNDERLYING_FAILED
        else:
            snapshot_status, wrapper_exit = "complete", c.EXIT_SUCCESS
        ctx.set_revert_capabilities()
        captured = evidence["psa_id_capture_status"] == "captured"
        if not captured:
            print("warning: assignment ID capture is incomplete; preserve the command result and identify the exact newly created assignment before manual recovery.", file=sys.stderr)

        ctx.manifest["snapshot_status"] = snapshot_status
        ctx.update_phase("underlying_command",
            status=snapshot_status, exit_code=exit_code, duration_seconds=duration,
            raw_artifact_paths=[str(ctx.snap_dir / "underlying-result.json")])
        ctx.update_phase("post_finalize", status="complete" if captured else "partial", duration_seconds=0.0,
                         psa_id_capture_status=evidence["psa_id_capture_status"])
        ctx.save()
        return wrapper_exit
    finally:
        ctx.release_lock()
