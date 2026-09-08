"""cli.py — `jsc` wrapper command entry point.

Subcommand registry:
  jsc revert-token grant   — mint TTL bypass token
  jsc revert-token show    — print current token
  jsc revert-token revoke  — delete current token
  jsc deploy               — sf project deploy start with snapshot
  jsc apex run             — sf apex run with snapshot
  jsc data update          — sf data update record with snapshot
  jsc data create          — sf data create record with snapshot
  jsc data delete          — sf data delete record with snapshot
  jsc revert show          — list snapshots
  jsc revert preview <id>  — preview revert plan
  jsc revert <id>          — execute revert
  jsc revert discard <id>  — mark snapshot abandoned

Future (Phase I.4-extended next session): jsc data upsert/import,
data bulk variants, org assign, package install/uninstall, deploy quick/resume,
deploy abort.

Usage as installed package:
  PYTHONPATH=packages/revert python3 -m jsc_revert.cli <subcommand> [args...]

Or via the `jsc` shell wrapper script (Phase I.4-extended-D ships).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import intent_marker
from . import org_detect


def cmd_revert_token_grant(args: argparse.Namespace) -> int:
    org = org_detect.resolve_org(args.org)
    if org is None:
        print(f"error: cannot resolve org alias {args.org!r}. "
              f"Run `sf org display --target-org {args.org}` to verify auth.",
              file=sys.stderr)
        return 1

    try:
        cmd_to_fingerprint = args.command if args.command else None
        path = intent_marker.mint(
            operation_type=args.operation_type,
            org_id_18=org.org_id_18,
            org_alias=org.alias,
            command=cmd_to_fingerprint,
            command_fingerprint=None if cmd_to_fingerprint else "sha256:" + ("0" * 64),
            reason=args.reason,
            expiry_seconds=args.expires_in * 60 if args.expires_in else None,
        )
    except ValueError as e:
        print(f"error: token mint failed: {e}", file=sys.stderr)
        return 1

    print(f"Token minted at: {path}")
    print(f"  operation_type: {args.operation_type}")
    print(f"  org_id_18:      {org.org_id_18} ({org.alias})")
    print(f"  reason:         {args.reason}")
    print(f"  expires:        {args.expires_in or '15'} minutes from now")
    if not cmd_to_fingerprint:
        print(f"  WARNING: --command not specified; manual-bypass token will allow ANY command "
              f"against this org. Consider passing --command for a tighter binding.")
    return 0


def cmd_revert_token_show(args: argparse.Namespace) -> int:
    token = intent_marker.show()
    if token is None:
        print("No token present.")
        return 0
    print(json.dumps(token, indent=2))
    return 0


def cmd_revert_token_revoke(args: argparse.Namespace) -> int:
    deleted = intent_marker.revoke()
    if deleted:
        print("Token revoked.")
    else:
        print("No token to revoke.")
    return 0


def cmd_deploy(args: argparse.Namespace) -> int:
    from .wrappers import deploy as wrapper
    return wrapper.run(args)


def cmd_apex_run(args: argparse.Namespace) -> int:
    from .wrappers import apex_run as wrapper
    return wrapper.run(args)


def cmd_data_update(args: argparse.Namespace) -> int:
    from .wrappers import data_update as wrapper
    return wrapper.run(args)


def cmd_data_create(args: argparse.Namespace) -> int:
    from .wrappers import data_create as wrapper
    return wrapper.run(args)


def cmd_data_delete(args: argparse.Namespace) -> int:
    from .wrappers import data_delete as wrapper
    return wrapper.run(args)


def cmd_data_undelete(args: argparse.Namespace) -> int:
    from .wrappers import data_undelete as wrapper
    return wrapper.run(args)


# ── Phase I.4-extended-2 (v7.17.0) wrappers ───────────────────────────────


def cmd_data_upsert(args: argparse.Namespace) -> int:
    from .wrappers import data_upsert as wrapper
    return wrapper.run(args)


def cmd_data_import(args: argparse.Namespace) -> int:
    from .wrappers import data_import as wrapper
    return wrapper.run(args)


def cmd_data_bulk_update(args: argparse.Namespace) -> int:
    from .wrappers import data_bulk_update as wrapper
    return wrapper.run(args)


def cmd_data_bulk_upsert(args: argparse.Namespace) -> int:
    from .wrappers import data_bulk_upsert as wrapper
    return wrapper.run(args)


def cmd_data_bulk_delete(args: argparse.Namespace) -> int:
    from .wrappers import data_bulk_delete as wrapper
    return wrapper.run(args)


def cmd_data_bulk_import(args: argparse.Namespace) -> int:
    from .wrappers import data_bulk_import as wrapper
    return wrapper.run(args)


def cmd_org_assign_permset(args: argparse.Namespace) -> int:
    from .wrappers import org_assign_permset as wrapper
    return wrapper.run(args)


def cmd_org_assign_permsetlicense(args: argparse.Namespace) -> int:
    from .wrappers import org_assign_permsetlicense as wrapper
    return wrapper.run(args)


def cmd_package_install(args: argparse.Namespace) -> int:
    from .wrappers import package_install as wrapper
    return wrapper.run(args)


def cmd_package_uninstall(args: argparse.Namespace) -> int:
    from .wrappers import package_uninstall as wrapper
    return wrapper.run(args)


def cmd_deploy_quick(args: argparse.Namespace) -> int:
    from .wrappers import deploy_quick as wrapper
    return wrapper.run(args)


def cmd_deploy_resume(args: argparse.Namespace) -> int:
    from .wrappers import deploy_resume as wrapper
    return wrapper.run(args)


def cmd_deploy_abort(args: argparse.Namespace) -> int:
    from .wrappers import deploy_abort as wrapper
    return wrapper.run(args)


def _proxy_post_deploy_main(args: list[str]) -> int:
    """Proxy CLI invocations to post_deploy_polling.main()."""
    from . import post_deploy_polling
    return post_deploy_polling.main(args)


def cmd_revert_show(args: argparse.Namespace) -> int:
    from . import manifest
    org = org_detect.resolve_org(args.org)
    if org is None:
        print(f"error: cannot resolve org alias {args.org!r}", file=sys.stderr)
        return 1
    snaps = manifest.list_snapshots(org.org_id_short, org.alias, limit=args.limit)
    if not snaps:
        print(f"No snapshots for {args.org} ({org.org_id_18}).")
        return 0
    print(f"Snapshots for {args.org} ({org.org_id_18}) — newest first:\n")
    for snap_dir, m in snaps:
        # Re-derived, like preview and exec — a listing that shows ✓ for a
        # snapshot the executor will refuse is worse than showing nothing.
        from . import revert_capabilities as _rcap
        revert_status = "✓" if _rcap.effective_capabilities(m).get("automatic_revertible") is True else "?"
        print(f"  [{m.get('snapshot_status', '?'):24}] {m['snapshot_id']}  "
              f"op={m['operation_type']:22} captured={m['captured_at']}  "
              f"auto-revert={revert_status}")
    return 0


def cmd_revert_preview(args: argparse.Namespace) -> int:
    from . import revert_planner
    return revert_planner.preview(args.snapshot_id, args.org)


def cmd_revert(args: argparse.Namespace) -> int:
    from . import revert_executor
    return revert_executor.execute_revert(
        snapshot_id=args.snapshot_id,
        target_org=args.org,
        force_ack=args.force,
        reason=args.reason or f"manual revert of {args.snapshot_id}",
    )


def cmd_revert_discard(args: argparse.Namespace) -> int:
    from . import manifest
    org = org_detect.resolve_org(args.org)
    if org is None:
        print(f"error: cannot resolve org alias {args.org!r}", file=sys.stderr)
        return 1
    try:
        snap_dir, m = manifest.load_by_id(org.org_id_short, org.alias, args.snapshot_id)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    m["snapshot_status"] = "abandoned"
    manifest.save(snap_dir, m)
    print(f"Snapshot {args.snapshot_id} marked abandoned.")
    return 0


def _add_revert_chain_args(p: argparse.ArgumentParser, default_operation_type: str) -> None:
    """Forensic revert-chain args accepted by every snapshot-creating wrapper.

    revert_executor appends --parent-snapshot-id / --invoking-intent /
    --operation-type uniformly to whatever revert_planner produces. Without these
    on the data subparsers, `jsc revert <id>` of a data_record_update /
    data_record_create snapshot died with argparse "unrecognized arguments"
    (audit 2026-06-09 REVERT-1). Defaults make normal (non-revert) invocations
    record their own operation_type; a revert passes --operation-type revert.
    """
    p.add_argument("--parent-snapshot-id", default=None, help="for revert chains (forensic)")
    p.add_argument("--invoking-intent", default=None, help="reason for this op")
    p.add_argument("--operation-type", default=default_operation_type, help="for revert chains")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jsc", description="Torque compatibility wrapper for snapshot-aware sf operations")
    sub = p.add_subparsers(dest="command", required=True)

    # ── revert-token group ────────────────────────────────────────────────
    rt = sub.add_parser("revert-token", help="manage TTL bypass tokens")
    rt_sub = rt.add_subparsers(dest="rt_action", required=True)

    rt_grant = rt_sub.add_parser("grant", help="mint a TTL bypass token")
    rt_grant.add_argument("--org", required=True, help="target org alias")
    rt_grant.add_argument("--reason", required=True, help="structured reason: 'JIRA-NNNN: <text>' or 'INC-NNNN: <text>' or 'ad-hoc: <≥20 chars>'")
    rt_grant.add_argument("--operation-type", default="manual-bypass",
                          choices=["revert", "recovery", "manual-bypass"])
    rt_grant.add_argument("--expires-in", type=int, help="TTL in minutes (capped per operation_type)")
    rt_grant.add_argument("--command", help="optional: bind token to a specific command (else manual-bypass allows any)")
    rt_grant.set_defaults(func=cmd_revert_token_grant)

    rt_show = rt_sub.add_parser("show", help="print current token")
    rt_show.set_defaults(func=cmd_revert_token_show)

    rt_revoke = rt_sub.add_parser("revoke", help="delete current token")
    rt_revoke.set_defaults(func=cmd_revert_token_revoke)

    # ── deploy ────────────────────────────────────────────────────────────
    d = sub.add_parser("deploy", help="sf project deploy start with snapshot")
    d.add_argument("--target-org", "-o", required=True, dest="target_org")
    d.add_argument("--metadata", action="append", default=[], help="metadata selector(s)")
    d.add_argument("--manifest", action="append", default=[], help="package.xml path(s)")
    d.add_argument("--source-dir", action="append", default=[], help="source dir(s)")
    d.add_argument("--pre-destructive-changes", default=None)
    d.add_argument("--dry-run", action="store_true")
    d.add_argument("--parent-snapshot-id", default=None, help="for revert chains (forensic)")
    d.add_argument("--invoking-intent", default=None, help="reason for this op")
    d.add_argument("--operation-type", default="deploy_metadata", help="for revert chains")
    d.set_defaults(func=cmd_deploy)

    # ── apex run ──────────────────────────────────────────────────────────
    a = sub.add_parser("apex", help="apex subcommands")
    a_sub = a.add_subparsers(dest="apex_action", required=True)
    a_run = a_sub.add_parser("run", help="sf apex run with snapshot")
    a_run.add_argument("--target-org", "-o", required=True, dest="target_org")
    a_run.add_argument("--file", "-f", required=True, dest="apex_file", help="path to .apex file")
    a_run.add_argument("--touches", default="", help="comma-separated objects this apex will touch (declared)")
    a_run.set_defaults(func=cmd_apex_run)

    # ── data subcommands ──────────────────────────────────────────────────
    dd = sub.add_parser("data", help="data subcommands")
    dd_sub = dd.add_subparsers(dest="data_action", required=True)

    du = dd_sub.add_parser("update", help="sf data update record with snapshot")
    du.add_argument("--target-org", "-o", required=True, dest="target_org")
    du.add_argument("--sobject", required=True)
    du.add_argument("--record-id", required=True)
    du.add_argument("--values", required=True, help="key=value pairs (sf data update format)")
    _add_revert_chain_args(du, "data_record_update")
    du.set_defaults(func=cmd_data_update)

    dc = dd_sub.add_parser("create", help="sf data create record with snapshot")
    dc.add_argument("--target-org", "-o", required=True, dest="target_org")
    dc.add_argument("--sobject", required=True)
    dc.add_argument("--values", required=True)
    _add_revert_chain_args(dc, "data_record_create")
    dc.set_defaults(func=cmd_data_create)

    de = dd_sub.add_parser("delete", help="sf data delete record with snapshot")
    de.add_argument("--target-org", "-o", required=True, dest="target_org")
    de.add_argument("--sobject", required=True)
    de.add_argument("--record-id", required=True)
    de.add_argument("--use-tooling-api", action="store_true")
    _add_revert_chain_args(de, "data_record_delete")
    de.set_defaults(func=cmd_data_delete)

    dun = dd_sub.add_parser("undelete", help="undelete a soft-deleted record via Apex (with snapshot)")
    dun.add_argument("--target-org", "-o", required=True, dest="target_org")
    dun.add_argument("--sobject", required=True)
    dun.add_argument("--record-id", required=True)
    _add_revert_chain_args(dun, "data_undelete")
    dun.set_defaults(func=cmd_data_undelete)

    # ── Phase I.4-extended-2 (v7.17.0): upsert/import + bulk variants ────
    du2 = dd_sub.add_parser("upsert", help="sf data upsert record with snapshot")
    du2.add_argument("--target-org", "-o", required=True, dest="target_org")
    du2.add_argument("--sobject", required=True)
    du2.add_argument("--external-id", required=True, help="external id field name or 'Field=Value'")
    du2.add_argument("--values", required=True)
    du2.set_defaults(func=cmd_data_upsert)

    di = dd_sub.add_parser("import", help="sf data import tree with snapshot")
    di.add_argument("--target-org", "-o", required=True, dest="target_org")
    di.add_argument("--plan", required=True, help="path to sObject-tree plan JSON")
    di.set_defaults(func=cmd_data_import)

    # data bulk subcommand group
    db = dd_sub.add_parser("bulk", help="bulk DML subcommands (with snapshot)")
    db_sub = db.add_subparsers(dest="bulk_action", required=True)

    dbu = db_sub.add_parser("update", help="sf data update bulk with before-row CSV")
    dbu.add_argument("--target-org", "-o", required=True, dest="target_org")
    dbu.add_argument("--sobject", required=True)
    dbu.add_argument("--file", required=True, help="path to input CSV")
    dbu.add_argument("--wait", type=int, default=30)
    dbu.set_defaults(func=cmd_data_bulk_update)

    dbup = db_sub.add_parser("upsert", help="sf data upsert bulk with external-id-keyed CSV")
    dbup.add_argument("--target-org", "-o", required=True, dest="target_org")
    dbup.add_argument("--sobject", required=True)
    dbup.add_argument("--external-id", required=True)
    dbup.add_argument("--file", required=True)
    dbup.add_argument("--wait", type=int, default=30)
    dbup.set_defaults(func=cmd_data_bulk_upsert)

    dbd = db_sub.add_parser("delete", help="sf data delete bulk with soft/hard mode")
    dbd.add_argument("--target-org", "-o", required=True, dest="target_org")
    dbd.add_argument("--sobject", required=True)
    dbd.add_argument("--file", required=True)
    dbd.add_argument("--hard-delete", action="store_true",
                    help="bypass Recycle Bin (non-revertible)")
    dbd.add_argument("--wait", type=int, default=30)
    dbd.set_defaults(func=cmd_data_bulk_delete)

    dbi = db_sub.add_parser("import", help="sf data import bulk from CSV")
    dbi.add_argument("--target-org", "-o", required=True, dest="target_org")
    dbi.add_argument("--sobject", required=True)
    dbi.add_argument("--file", required=True)
    dbi.add_argument("--wait", type=int, default=30)
    dbi.set_defaults(func=cmd_data_bulk_import)

    # ── org subcommands (v7.17.0) ────────────────────────────────────────
    org = sub.add_parser("org", help="org-level subcommands (with snapshot)")
    org_sub = org.add_subparsers(dest="org_action", required=True)
    org_assign = org_sub.add_parser("assign", help="assign permission sets / licenses")
    org_assign_sub = org_assign.add_subparsers(dest="assign_action", required=True)

    oap = org_assign_sub.add_parser("permset", help="sf org assign permset with PSA-id capture")
    oap.add_argument("--target-org", "-o", required=True, dest="target_org")
    oap.add_argument("--name", required=True, dest="perm_set_name", help="permission set API name")
    # Codex-R1-P2-02: forward as --on-behalf-of to underlying sf CLI; accept legacy --on-user as alias
    oap.add_argument("--on-behalf-of", "-b", default=None, dest="on_behalf_of",
                     help="optional: target username/alias (sf CLI --on-behalf-of)")
    oap.add_argument("--on-user", default=None, dest="on_user",
                     help="DEPRECATED alias for --on-behalf-of")
    oap.set_defaults(func=cmd_org_assign_permset)

    oapl = org_assign_sub.add_parser("permsetlicense", help="sf org assign permsetlicense with PSL-id capture")
    oapl.add_argument("--target-org", "-o", required=True, dest="target_org")
    oapl.add_argument("--name", required=True, dest="license_name", help="permission set license name")
    oapl.add_argument("--on-behalf-of", "-b", default=None, dest="on_behalf_of")
    oapl.add_argument("--on-user", default=None, dest="on_user", help="DEPRECATED alias")
    oapl.set_defaults(func=cmd_org_assign_permsetlicense)

    # ── package subcommands (v7.17.0) ────────────────────────────────────
    pkg = sub.add_parser("package", help="package install/uninstall (non-revertible)")
    pkg_sub = pkg.add_subparsers(dest="package_action", required=True)

    pkg_install = pkg_sub.add_parser("install", help="sf package install with installed-list pre-snapshot")
    pkg_install.add_argument("--target-org", "-o", required=True, dest="target_org")
    pkg_install.add_argument("--package", required=True, help="package ID (04t...) or alias")
    pkg_install.add_argument("--wait", type=int, default=30, help="wait minutes")
    pkg_install.set_defaults(func=cmd_package_install)

    pkg_uninstall = pkg_sub.add_parser("uninstall", help="sf package uninstall (non-revertible)")
    pkg_uninstall.add_argument("--target-org", "-o", required=True, dest="target_org")
    pkg_uninstall.add_argument("--package", required=True)
    pkg_uninstall.add_argument("--wait", type=int, default=30)
    pkg_uninstall.set_defaults(func=cmd_package_uninstall)

    # ── deploy quick/resume/abort (v7.17.0) ──────────────────────────────
    # Re-open the `d` parser group with extra subcommands. Since the existing
    # `deploy` parser doesn't have subparsers (it's a flat command), we'd need
    # a sibling-namespace. The clean approach is `jsc deploy-quick`, etc.
    dq = sub.add_parser("deploy-quick", help="sf project deploy quick (promote a validation)")
    dq.add_argument("--target-org", "-o", required=True, dest="target_org")
    dq.add_argument("--job-id", required=True, help="validation deploy id to promote")
    dq.add_argument("--wait", type=int, default=33)
    dq.set_defaults(func=cmd_deploy_quick)

    dr = sub.add_parser("deploy-resume", help="sf project deploy resume (poll async deploy)")
    dr.add_argument("--target-org", "-o", required=True, dest="target_org")
    dr.add_argument("--job-id", required=True)
    dr.add_argument("--wait", type=int, default=33)
    dr.set_defaults(func=cmd_deploy_resume)

    da = sub.add_parser("deploy-abort", help="sf project deploy cancel an in-flight deploy")
    da.add_argument("--target-org", "-o", required=True, dest="target_org")
    da.add_argument("--job-id", required=True)
    da.add_argument("--wait", type=int, default=33)
    da.set_defaults(func=cmd_deploy_abort)

    # ── post-deploy polling (v7.17.0) ────────────────────────────────────
    pdp = sub.add_parser("post-deploy", help="background polling for async sf ops")
    pdp_sub = pdp.add_subparsers(dest="post_deploy_action", required=True)

    pdp_poll = pdp_sub.add_parser("poll", help="run one polling pass + exit")
    pdp_poll.add_argument("--queue-dir", default=None)
    pdp_poll.set_defaults(func=lambda a: _proxy_post_deploy_main(["poll"] +
        (["--queue-dir", a.queue_dir] if a.queue_dir else [])))

    pdp_daemon = pdp_sub.add_parser("daemon", help="loop polling until SIGINT/SIGTERM")
    pdp_daemon.add_argument("--queue-dir", default=None)
    pdp_daemon.add_argument("--interval", type=int, default=60)
    pdp_daemon.add_argument("--timeout", type=int, default=0,
        help="0 = forever; otherwise exit after N seconds")
    pdp_daemon.set_defaults(func=lambda a: _proxy_post_deploy_main(["daemon",
        "--interval", str(a.interval), "--timeout", str(a.timeout)] +
        (["--queue-dir", a.queue_dir] if a.queue_dir else [])))

    pdp_show = pdp_sub.add_parser("show", help="list queue entries")
    pdp_show.add_argument("--queue-dir", default=None)
    pdp_show.set_defaults(func=lambda a: _proxy_post_deploy_main(["show"] +
        (["--queue-dir", a.queue_dir] if a.queue_dir else [])))

    # ── revert subcommands ───────────────────────────────────────────────
    rv = sub.add_parser("revert", help="snapshot revert subcommands (or revert <id>)")
    rv_sub = rv.add_subparsers(dest="revert_action", required=True)

    rv_show = rv_sub.add_parser("show", help="list snapshots")
    rv_show.add_argument("--org", required=True)
    rv_show.add_argument("--limit", type=int, default=20)
    rv_show.set_defaults(func=cmd_revert_show)

    rv_preview = rv_sub.add_parser("preview", help="preview revert plan")
    rv_preview.add_argument("snapshot_id")
    rv_preview.add_argument("--org", required=True)
    rv_preview.set_defaults(func=cmd_revert_preview)

    rv_exec = rv_sub.add_parser("exec", help="execute revert (or use 'jsc revert' shorthand)")
    rv_exec.add_argument("snapshot_id")
    rv_exec.add_argument("--org", required=True)
    rv_exec.add_argument("--force", action="store_true",
                         help="proceed even with absent/renamed_unknown components")
    rv_exec.add_argument("--reason", default=None)
    rv_exec.set_defaults(func=cmd_revert)

    rv_discard = rv_sub.add_parser("discard", help="mark snapshot abandoned")
    rv_discard.add_argument("snapshot_id")
    rv_discard.add_argument("--org", required=True)
    rv_discard.set_defaults(func=cmd_revert_discard)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    from .org_sequence import LockOwnershipError
    try:
        return args.func(args)
    except LockOwnershipError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 40


if __name__ == "__main__":
    sys.exit(main())
