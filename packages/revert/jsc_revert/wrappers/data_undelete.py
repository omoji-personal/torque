"""jsc data undelete wrapper — undelete a soft-deleted record via anonymous Apex.

B4: the dedicated revert path for a soft `data_record_delete` snapshot. There is
no `sf data undelete record` equivalent, so this wrapper executes anonymous Apex
internally (via the same sf-subprocess mechanism the other wrappers use) and runs
through the SAME pre-snapshot + org-lock + finalize path as data_delete.py. Its
OWN snapshot/lock is the safety boundary — like every wrapper, it self-snapshots
and is therefore EXEMPT from revert_warning routing.

S-3 hardening: inputs are validated BEFORE the Apex string is built. The
record_id is bound via a SOQL bind variable (`:rid`), never string-interpolated
into the query; `ALL ROWS` sits INSIDE the SOQL brackets so the soft-deleted
(Recycle Bin) row is actually selected for undelete.
"""

from __future__ import annotations

import argparse
import re
import sys
import time

from . import _common as c


# Salesforce API name: starts with a letter, then letters/digits/underscores,
# optional custom suffix. Covers standard objects (Contact, Account) and custom
# objects (Demo__Intake__c, My_Object__c).
_API_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(__c|__C)?$")

# Salesforce record id: 15 (case-sensitive) or 18 (case-insensitive) chars,
# alphanumeric only.
_SF_ID_RE = re.compile(r"^[A-Za-z0-9]{15}$|^[A-Za-z0-9]{18}$")


def _validate_object_api_name(name: str) -> bool:
    return bool(name) and _API_NAME_RE.match(name) is not None


def _validate_record_id(rid: str) -> bool:
    return bool(rid) and _SF_ID_RE.match(rid) is not None


def build_undelete_apex(object_api_name: str, record_id: str) -> str:
    """Build the anonymous Apex undelete script (S-3 hardened).

    Caller MUST have validated object_api_name + record_id first. The id is
    passed through a SOQL bind var (:rid), and `ALL ROWS` is INSIDE the SOQL
    brackets so the Recycle-Bin row is in scope for undelete.
    """
    if not _validate_object_api_name(object_api_name):
        raise ValueError(f"invalid object_api_name: {object_api_name!r}")
    if not _validate_record_id(record_id):
        raise ValueError(f"invalid record_id: {record_id!r}")
    return (
        f"Id rid = '{record_id}';\n"
        f"undelete [SELECT Id FROM {object_api_name} WHERE Id = :rid ALL ROWS];"
    )


def run(args: argparse.Namespace) -> int:
    # ── Validate inputs FIRST (S-3) ───────────────────────────────────────
    if not _validate_object_api_name(args.sobject):
        print(f"error: invalid --sobject API name: {args.sobject!r}", file=sys.stderr)
        return c.EXIT_PRESNAP_FAILED_PROD
    if not _validate_record_id(args.record_id):
        print(f"error: invalid --record-id (must be 15/18-char Salesforce id): "
              f"{args.record_id!r}", file=sys.stderr)
        return c.EXIT_PRESNAP_FAILED_PROD

    wrapper_command = (
        f"jsc data undelete -o {args.target_org} --sobject {args.sobject} "
        f"--record-id {args.record_id}"
    )

    op = getattr(args, "operation_type", None) or "data_undelete"
    invoking_intent = None
    if getattr(args, "invoking_intent", None):
        invoking_intent = {"kind": "user-direct", "reason": args.invoking_intent, "token_fingerprint": None}
    if op == "revert" and getattr(args, "parent_snapshot_id", None):
        invoking_intent = {"kind": "revert-of", "reason": f"revert-of {args.parent_snapshot_id}", "token_fingerprint": None}

    ctx = c.WrapperContext(
        operation_type=op,
        target_org=args.target_org,
        wrapper_command=wrapper_command,
        invoking_intent=invoking_intent,
        parent_snapshot_id=getattr(args, "parent_snapshot_id", None),
    )
    rc = ctx.resolve_org()
    if rc: return rc
    rc = ctx.acquire_org_lock()
    if rc: return rc

    try:
        ctx.init_snapshot_dir()

        # ── Pre-snapshot ──────────────────────────────────────────────────
        # Build the (validated) undelete Apex now; persist it for forensics.
        apex_code = build_undelete_apex(args.sobject, args.record_id)
        (ctx.snap_dir / "undelete_input.apex").write_text(apex_code)

        ctx.manifest["payload"] = {
            "object_api_name": args.sobject,
            "record_id": args.record_id,
            "external_id_field": None,
            "before_row": None,  # record is soft-deleted (in Recycle Bin) right now
            "after_row": None,
            "delete_mode": "soft",  # undelete only undoes a SOFT delete
            "fields_captured": [],
            "undelete_apex_path": str(ctx.snap_dir / "undelete_input.apex"),
        }
        ctx.set_revert_capabilities()
        ctx.update_phase("pre_snapshot", status="complete", duration_seconds=0.0)
        ctx.save()

        # ── Underlying: anonymous Apex undelete ───────────────────────────
        t0 = time.monotonic()
        cmd = ["sf", "apex", "run",
               "--target-org", args.target_org,
               "--file", str(ctx.snap_dir / "undelete_input.apex"),
               "--json"]
        exit_code, stdout, stderr = c.run_sf_subprocess(cmd, timeout_seconds=120)
        duration = round(time.monotonic() - t0, 2)
        (ctx.snap_dir / "underlying-result.json").write_text(stdout)

        sf_json = c.parse_sf_json_safely(stdout)
        compiled_ok = True
        if sf_json:
            r = sf_json.get("result", {})
            # apex run reports compile/exec success under result.success / .compiled
            compiled_ok = bool(r.get("success", True)) and bool(r.get("compiled", True))

        snapshot_status = "complete" if (exit_code == 0 and compiled_ok) else "failed"
        ctx.manifest["snapshot_status"] = snapshot_status
        ctx.update_phase("underlying_command",
            status=snapshot_status, exit_code=exit_code, duration_seconds=duration,
            raw_artifact_paths=[str(ctx.snap_dir / "underlying-result.json")],
        )

        # ── Post-finalize: capture the now-undeleted row ──────────────────
        t0 = time.monotonic()
        if snapshot_status == "complete":
            after_row = _get_record(args.target_org, args.sobject, args.record_id)
            ctx.manifest["payload"]["after_row"] = after_row
        ctx.update_phase("post_finalize",
            status="complete", duration_seconds=round(time.monotonic() - t0, 2),
        )
        ctx.save()

        if snapshot_status == "complete":
            print(f"undeleted: {args.sobject} {args.record_id} "
                  f"(snapshot: {ctx.snapshot_id})", file=sys.stderr)
        return c.EXIT_SUCCESS if snapshot_status == "complete" else c.EXIT_UNDERLYING_FAILED

    finally:
        ctx.release_lock()


def _get_record(target_org: str, sobject: str, record_id: str) -> dict | None:
    cmd = ["sf", "data", "get", "record",
           "--target-org", target_org, "--sobject", sobject, "--record-id", record_id, "--json"]
    code, stdout, _ = c.run_sf_subprocess(cmd, timeout_seconds=30)
    if code != 0:
        return None
    import json
    try:
        return json.loads(stdout).get("result", {})
    except json.JSONDecodeError:
        return None
