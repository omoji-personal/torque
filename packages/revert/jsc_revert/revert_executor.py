"""revert_executor.py — execute revert from snapshot.

Closes plan-v5 Closure 6 (parent_snapshot_id transmission). Per Gemini-R4-P1-1
fix: does NOT mint TTL token (PreToolUse hooks fire on Claude `<Bash>` calls,
not Python subprocess.run). Subprocess invocation of wrappers naturally bypasses
deploy_gate (different command pattern: `jsc deploy` vs `sf project deploy`).

Stale-revert defense: by default refuses to revert if any component is absent
or renamed_unknown. Operator overrides with --force.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import manifest as mf, org_detect, revert_capabilities, revert_planner, stale_detector


EXIT_SUCCESS = 0
EXIT_NOT_REVERTIBLE = 51
EXIT_STALE_BLOCKED = 50
EXIT_ORG_RESOLUTION_FAILED = 1
EXIT_SNAPSHOT_NOT_FOUND = 1


def _append_forensic_chain(revert_cmd: list[str], snapshot_id: str, reason: str | None) -> list[str]:
    """Append the forensic revert-chain flags every snapshot wrapper accepts.

    Idempotent: skips any flag already present (deploy_metadata's planner output
    pre-includes them). Factored out so the executor->CLI seam is unit-testable
    (audit 2026-06-09 TEST-1 — the seam that let REVERT-1 ship was uncovered).
    """
    cmd = list(revert_cmd)
    if "--parent-snapshot-id" not in cmd:
        cmd += ["--parent-snapshot-id", snapshot_id]
    if "--invoking-intent" not in cmd:
        cmd += ["--invoking-intent", reason or f"revert-of {snapshot_id}"]
    if "--operation-type" not in cmd:
        cmd += ["--operation-type", "revert"]
    return cmd


def execute_revert(
    snapshot_id: str,
    target_org: str,
    force_ack: bool = False,
    reason: str | None = None,
) -> int:
    """Execute a revert. Returns wrapper exit code or EXIT_* sentinel."""
    org = org_detect.resolve_org(target_org)
    if org is None:
        print(f"error: cannot resolve org {target_org!r}", file=sys.stderr)
        from .wrappers._common import release_after_resolution_failure
        release_after_resolution_failure(target_org)
        return EXIT_ORG_RESOLUTION_FAILED

    try:
        snap_dir, snap = mf.load_by_id(org.org_id_short, org.alias, snapshot_id)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_SNAPSHOT_NOT_FOUND

    # The resolved explicit target controls execution, including renamed aliases.
    if snap.get("org", {}).get("org_id_18", "")[:15] != org.org_id_short:
        print("error: snapshot belongs to a different org than the explicit target", file=sys.stderr)
        return EXIT_ORG_RESOLUTION_FAILED
    snap = {**snap, "org": {**snap["org"], "alias": target_org}}

    # Connected mode: this run needs the approval the gate just consumed for it, for
    # this org ID; the wrapper it starts is told which approval that was.
    from .wrappers._common import APPROVED_PARENT_ENV, connected_approval
    approved_rc, approved = connected_approval(target_org, org.org_id_18)
    if approved_rc:
        return approved_rc

    # Refuse non-revertible operations unless force-acked.
    # Re-derived, NOT read from the manifest: this is the gate that decides
    # whether a revert actually runs, so a stale stored `true` from a snapshot
    # written before the capture-aware deploy gate must not be able to open it.
    # Preview and `revert-show` go through the same helper so the three cannot
    # drift into disagreeing about the same snapshot.
    rc = revert_capabilities.effective_capabilities(snap)
    if rc.get("automatic_revertible") is False:
        if not force_ack:
            print(f"\nThis snapshot's operation_type ({snap['operation_type']}) is NOT "
                  f"automatically revertible.", file=sys.stderr)
            if rc.get("manual_recovery_path"):
                print(f"\nManual recovery guidance:\n  {rc['manual_recovery_path']}", file=sys.stderr)
            print(f"\nTo force a best-effort attempt anyway, re-run with --force "
                  f"(NOT recommended for production).", file=sys.stderr)
            return EXIT_NOT_REVERTIBLE
        print(f"warning: --force overriding automatic_revertible=False; this is best-effort only.",
              file=sys.stderr)

    # Drift check (only meaningful for metadata)
    if snap["operation_type"] == "deploy_metadata":
        print(f"checking drift on {target_org}...", file=sys.stderr)
        drift = stale_detector.classify_metadata_drift(snap_dir, snap, target_org)
        summary = stale_detector.summary(drift)
        print(f"drift summary: {summary}", file=sys.stderr)
        if stale_detector.is_blocking(drift) and not force_ack:
            print(f"\nStale revert blocked: {summary['absent']} absent + "
                  f"{summary['renamed_unknown']} renamed_unknown components.",
                  file=sys.stderr)
            print(f"Re-run with --force to proceed (will recreate absent components).",
                  file=sys.stderr)
            return EXIT_STALE_BLOCKED

    # Build the revert command
    revert_cmd = revert_planner.build_revert_command(snap, snap_dir)
    if revert_cmd is None:
        print(f"error: no revert plan available for operation_type {snap['operation_type']}",
              file=sys.stderr)
        return EXIT_NOT_REVERTIBLE

    # Add forensic chain: parent_snapshot_id + invoking_intent + operation_type=revert.
    # All snapshot-creating wrappers now accept these (cli._add_revert_chain_args);
    # the deploy wrapper pre-includes them so the append stays idempotent
    # (audit 2026-06-09 REVERT-1 — data update/create/delete previously rejected them).
    revert_cmd = _append_forensic_chain(revert_cmd, snapshot_id, reason)
    if approved is not None:
        # Name the one wrapper command this approval covers; the child accepts only it.
        from torque.approval import authorize_child
        prefix = len(revert_planner._jsc_command())
        workspace_root, client_slug = approved["_scope"]
        authorize_child(workspace_root, client_slug, approved["id"], revert_cmd[prefix:])

    print(f"\nExecuting revert: {' '.join(revert_cmd)}\n", file=sys.stderr)

    # Per Gemini-R4-P1-1: NO token minting here. PreToolUse hooks fire on
    # Claude <Bash> calls only; this subprocess invocation never triggers
    # any hook. Deploy_gate.py does NOT match `jsc deploy` (only matches
    # `sf project deploy`), so wrapper invocations bypass naturally.
    #
    # Per audit codex-R3-P1-20 + gemini-R4: enforce subprocess discipline.
    # Close stdin (DEVNULL) so a malformed revert tool can't hang on read;
    # capture stdout/stderr; apply a generous timeout for revert operations.
    REVERT_TIMEOUT_S = int(os.environ.get("JSC_REVERT_EXECUTE_TIMEOUT_S", "1800"))
    try:
        child_env = dict(os.environ)
        child_env.pop(APPROVED_PARENT_ENV, None)
        if approved is not None:
            child_env[APPROVED_PARENT_ENV] = approved["id"]
        proc = subprocess.run(
            revert_cmd,
            env=child_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=REVERT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as e:
        print(f"REVERT TIMEOUT after {REVERT_TIMEOUT_S}s", file=sys.stderr)
        if e.stdout:
            partial = e.stdout if isinstance(e.stdout, str) else e.stdout.decode(errors='replace')
            print(f"  partial stdout: {partial[:1000]}", file=sys.stderr)
        if e.stderr:
            partial = e.stderr if isinstance(e.stderr, str) else e.stderr.decode(errors='replace')
            print(f"  partial stderr: {partial[:1000]}", file=sys.stderr)
        return -1
    except FileNotFoundError as exc:
        print(f"REVERT command not found: {exc}", file=sys.stderr)
        return -2
    if proc.stdout:
        print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    return proc.returncode
