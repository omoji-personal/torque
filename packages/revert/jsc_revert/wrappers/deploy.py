"""jsc deploy wrapper — sf project deploy start with snapshot."""

from __future__ import annotations

import argparse
import shlex
import sys
import time
from pathlib import Path

from . import _common as c
from .. import deploy_stage, snapshot_pre, stale_detector


def _options(args: argparse.Namespace) -> list[str]:
    options = []
    for key in ('test_level', 'api_version', 'post_destructive_changes'):
        value = getattr(args, key, None)
        if value:
            options += ['--' + key.replace('_', '-'), value]
    for name in getattr(args, 'tests', []):
        options += ['--tests', name]
    for key in ('ignore_conflicts', 'ignore_warnings'):
        if getattr(args, key, False):
            options.append('--' + key.replace('_', '-'))
    if getattr(args, 'async_deploy', False):
        options.append('--async')
    else:
        options += ['--wait', str(getattr(args, 'wait', 33))]
    return options


def run(args: argparse.Namespace) -> int:
    # Build wrapper_command for forensic record
    parts = ["jsc", "deploy", "-o", args.target_org]
    for m in args.metadata: parts += ["--metadata", m]
    for mn in args.manifest: parts += ["--manifest", mn]
    for sd in args.source_dir: parts += ["--source-dir", sd]
    if args.pre_destructive_changes: parts += ["--pre-destructive-changes", args.pre_destructive_changes]
    if args.dry_run: parts.append("--dry-run")
    options = _options(args)
    wrapper_command = shlex.join(parts + options)

    invoking_intent = None
    if args.invoking_intent:
        invoking_intent = {"kind": "user-direct", "reason": args.invoking_intent, "token_fingerprint": None}
    if args.operation_type == "revert" and args.parent_snapshot_id:
        invoking_intent = {"kind": "revert-of", "reason": f"revert-of {args.parent_snapshot_id}", "token_fingerprint": None}

    ctx = c.WrapperContext(
        operation_type=args.operation_type,
        target_org=args.target_org,
        wrapper_command=wrapper_command,
        invoking_intent=invoking_intent,
        parent_snapshot_id=args.parent_snapshot_id,
        dry_run=args.dry_run,
    )

    rc = ctx.resolve_org()
    if rc: return rc

    rc = ctx.acquire_org_lock()
    if rc: return rc

    try:
        ctx.init_snapshot_dir()
        ctx.manifest["payload"] = {
            "selectors": {
                "metadata_args": args.metadata,
                "manifest_paths": args.manifest,
                "destructive_manifest_paths": [path for path in (args.pre_destructive_changes,
                    getattr(args, 'post_destructive_changes', None)) if path],
                "source_dirs": args.source_dir,
                "target_org": args.target_org,
                "deploy_options": options,
                "dry_run": args.dry_run,
            },
            "deploy_id": None,
            "deploy_status_code": None,
            "recovery_readiness": "in_flight",
            "files": [],
            "messages": [],
        }
        # Provisional only. At this point payload["files"] is necessarily empty
        # — the retrieve below has not run — so this classifies as NOT
        # revertible. That is the honest reading of the state right now, and it
        # is also the value that survives if the process dies mid-flight, which
        # is the right way to fail. It is recomputed after Phase 1.
        ctx.set_revert_capabilities()
        ctx.save()

        # ── Phase 1: pre-snapshot retrieve ─────────────────────────────────
        if args.metadata and not args.dry_run:
            t0 = time.monotonic()
            metadata_dir = ctx.snap_dir / "metadata-before"
            metadata_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                retrieve_result = snapshot_pre.run_pre_snapshot_retrieve(
                    args.metadata, args.target_org, metadata_dir, timeout_seconds=180,
                )
                ctx.manifest["payload"]["files"] = [
                    {"type": fc.type, "fullName": fc.fullName, "state": "Pre",
                     "before_state": fc.state,
                     "before_checksum": fc.checksum, "filePath": fc.file_path}
                    for fc in retrieve_result.files
                ]
                ctx.update_phase("pre_snapshot",
                    status="complete",
                    duration_seconds=round(time.monotonic() - t0, 2),
                    raw_artifact_paths=[str(retrieve_result.raw_json_path)],
                )
                # Recompute now that capture has either produced files or not.
                # This is the ONLY point at which deploy_metadata can honestly
                # claim revertibility, and note it is evidence-driven rather
                # than selector-driven: a --metadata retrieve that returns an
                # empty file set still classifies as NOT revertible, because an
                # empty result and a complete one are indistinguishable to
                # snapshot_pre (Codex R2-IMP-03).
                ctx.set_revert_capabilities()
                ctx.save()
            except (ValueError, Exception) as e:
                ctx.update_phase("pre_snapshot",
                    status="failed",
                    duration_seconds=round(time.monotonic() - t0, 2),
                    error=str(e)[:500],
                )
                ctx.manifest["snapshot_status"] = "failed"
                ctx.save()
                if ctx.org.is_production:
                    print(f"error: pre-snapshot failed against production org: {e}", file=sys.stderr)
                    return c.EXIT_PRESNAP_FAILED_PROD
                # Nonproduction: emit warning + proceed
                print(f"warning: pre-snapshot failed (nonproduction; proceeding): {e}", file=sys.stderr)

        # ── Phase 2: invoke sf project deploy start ─────────────────────────
        # A revert deploys from the snapshot store, and the operator's cwd is
        # wherever they happened to be — usually not an SFDX project, which
        # `sf project deploy start` hard-requires. Stage one when needed so the
        # revert path works from any directory. Recovery also stages when the
        # caller is inside an unrelated project: only captured files belong in
        # that deploy. Only source-dir deploys can be
        # staged: --metadata and --manifest select against the CALLER's project,
        # so relocating them would change what gets deployed.
        stage_dir = None
        source_dirs = list(args.source_dir)
        if source_dirs and not args.metadata and not args.manifest \
                and (args.operation_type == "revert" or not c.in_sfdx_project()):
            stage_dir, source_dirs = c.stage_source_project(source_dirs)
            # Keep the short temporary path: moving into a deep snapshot can
            # exceed Windows MAX_PATH. Report needs this project until terminal.
            deploy_stage.retain(stage_dir, ctx.manifest)
        report_cwd = stage_dir or str(Path.cwd())
        ctx.manifest['payload']['report_cwd'] = report_cwd

        sf_cmd = ["sf", "project", "deploy", "start", "--target-org", args.target_org, "--json"]
        for m in args.metadata: sf_cmd += ["--metadata", m]
        for mn in args.manifest: sf_cmd += ["--manifest", mn]
        for sd in source_dirs: sf_cmd += ["--source-dir", sd]
        if args.pre_destructive_changes: sf_cmd += ["--pre-destructive-changes", args.pre_destructive_changes]
        if args.dry_run: sf_cmd.append("--dry-run")
        # Submission never waits for deployment completion. Save its ID before
        # a separate report call so a client timeout cannot lose the handle.
        sf_cmd += options[:-1] if getattr(args, 'async_deploy', False) else options[:-2]
        sf_cmd.append('--async')
        if stage_dir:
            for flag in ('--pre-destructive-changes', '--post-destructive-changes'):
                if flag in sf_cmd:
                    index = sf_cmd.index(flag) + 1
                    sf_cmd[index] = str(Path(sf_cmd[index]).absolute())

        t0 = time.monotonic()
        exit_code, stdout, stderr = c.run_sf_subprocess(
            sf_cmd, timeout_seconds=1800, cwd=stage_dir)
        duration = round(time.monotonic() - t0, 2)

        raw_path = ctx.snap_dir / "underlying-result.json"
        c.bundle.atomic_write_json(ctx.snap_dir / "underlying-command.json",
            {"command": sf_cmd, "exit_code": exit_code, "stdout": stdout, "stderr": stderr})
        raw_path.write_text(stdout, encoding="utf-8")
        sf_json = c.parse_sf_json_safely(stdout)
        from ..job_outcomes import deploy_outcome
        snapshot_status, deploy_id = deploy_outcome(sf_json, exit_code)
        ctx.manifest['payload']['deploy_id'] = deploy_id
        ctx.manifest['payload']['recovery_readiness'] = 'unknown'
        ctx.manifest['snapshot_status'] = snapshot_status
        ctx.set_revert_capabilities()
        ctx.save()
        c.auto_enqueue_if_pending(ctx, snapshot_status, deploy_id,
            ['sf', 'project', 'deploy', 'report', '--target-org', args.target_org,
             '--job-id', deploy_id, '--json'])
        ctx.save()
        if snapshot_status == 'pending_finalize_required' and deploy_id and not getattr(args, 'async_deploy', False):
            wait = getattr(args, 'wait', 33)
            report_cmd = ['sf', 'project', 'deploy', 'report', '--target-org', args.target_org,
                          '--job-id', deploy_id, '--wait', str(wait), '--json']
            exit_code, stdout, stderr = c.run_sf_subprocess(report_cmd, timeout_seconds=wait * 60 + 600, cwd=report_cwd)
            raw_path = ctx.snap_dir / 'deploy-report.json'
            c.bundle.atomic_write_json(raw_path, {'command': report_cmd, 'exit_code': exit_code,
                                                'stdout': stdout, 'stderr': stderr})
            sf_json = c.parse_sf_json_safely(stdout)
            snapshot_status, _ = deploy_outcome(sf_json, exit_code, deploy_id)
            duration = round(time.monotonic() - t0, 2)
        result = sf_json.get('result') if isinstance(sf_json, dict) else None
        wrapper_exit = c._outcome_exit(snapshot_status)
        ctx.manifest["snapshot_status"] = snapshot_status
        ctx.manifest["payload"]["deploy_id"] = deploy_id
        if isinstance(result, dict):
            ctx.manifest["payload"]["deploy_status_code"] = result.get("status")
        ctx.update_phase("underlying_command",
            status=snapshot_status,
            exit_code=exit_code,
            duration_seconds=duration,
            raw_artifact_paths=[str(raw_path)],
        )

        # ── Phase 3: post-finalize ─────────────────────────────────────────
        ctx.update_phase("post_finalize",
            status="complete" if wrapper_exit == 0 else "deferred",
            duration_seconds=0.0,
        )
        if wrapper_exit == 0 and args.metadata and not args.dry_run:
            try:
                stale_detector.capture_deployment_state(ctx.snap_dir, ctx.manifest, args.target_org)
                ctx.manifest['payload']['recovery_readiness'] = 'captured'
            except Exception as exc:
                snapshot_status = ctx.manifest["snapshot_status"] = "partial"
                ctx.update_phase("post_finalize", status="failed", error=str(exc))
                wrapper_exit = c.EXIT_POST_FINALIZE_FAILED
                print("Deployment succeeded, but post-deployment capture failed; do not retry the write.", file=sys.stderr)
        if snapshot_status == 'pending_finalize_required' or (not deploy_id and exit_code == -1):
            print('warning: deploy outcome is unknown; the server-side deploy may still be running. '
                  'Inspect deployment status in the org before retrying.', file=sys.stderr)
        ctx.set_revert_capabilities()
        ctx.save()
        deploy_stage.cleanup(ctx.manifest)

        if wrapper_exit != 0:
            c.print_sf_failure(sf_json, stderr)
            print(f"warning: deploy returned status={snapshot_status}, wrapper_exit={wrapper_exit}", file=sys.stderr)
        else:
            print(f"deploy success: snapshot {ctx.snapshot_id} captured at {ctx.snap_dir}", file=sys.stderr)
        return wrapper_exit

    finally:
        ctx.release_lock()
