#!/usr/bin/env python3
"""Tests for Phase I.4-extended-2 wrappers + post-deploy polling (v7.17.0).

OFFLINE only — no live sf CLI calls. Exercises:
- Wrapper module imports + run() signature (parametric, all 13 new wrappers)
- CLI subcommand parsing for new commands
- Post-deploy polling: queue mint, poll_once on empty queue, exp_backoff, etc.
- Revert capabilities classification for new op-types
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _run_cli(args: list[str], env_overrides: dict | None = None) -> tuple[int, str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]) + os.pathsep + os.environ.get("PYTHONPATH", "")
    if env_overrides:
        env.update(env_overrides)
    proc = subprocess.run(
        [sys.executable, "-m", "jsc_revert.cli", *args],
        capture_output=True, text=True, env=env, timeout=15,
    )
    return proc.returncode, proc.stdout, proc.stderr


def main() -> int:
    failures = 0
    tests = []

    def check(label: str, condition: bool):
        nonlocal failures
        tests.append((label, condition))
        if not condition:
            failures += 1

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    # ── WRAPPER MODULE IMPORTS ───────────────────────────────────────────
    NEW_WRAPPERS = [
        "data_upsert", "data_import", "data_bulk_update", "data_bulk_upsert",
        "data_bulk_delete", "data_bulk_import", "org_assign_permset",
        "org_assign_permsetlicense", "package_install", "package_uninstall",
        "deploy_quick", "deploy_resume", "deploy_abort",
    ]
    from jsc_revert.wrappers import (
        data_upsert, data_import, data_bulk_update, data_bulk_upsert,
        data_bulk_delete, data_bulk_import, org_assign_permset,
        org_assign_permsetlicense, package_install, package_uninstall,
        deploy_quick, deploy_resume, deploy_abort,
    )
    wrapper_modules = {
        "data_upsert": data_upsert, "data_import": data_import,
        "data_bulk_update": data_bulk_update, "data_bulk_upsert": data_bulk_upsert,
        "data_bulk_delete": data_bulk_delete, "data_bulk_import": data_bulk_import,
        "org_assign_permset": org_assign_permset,
        "org_assign_permsetlicense": org_assign_permsetlicense,
        "package_install": package_install, "package_uninstall": package_uninstall,
        "deploy_quick": deploy_quick, "deploy_resume": deploy_resume,
        "deploy_abort": deploy_abort,
    }
    for name, mod in wrapper_modules.items():
        check(f"F-WR-{name} module imports", mod is not None)
        check(f"F-WR-{name}-run module exposes run()", callable(getattr(mod, "run", None)))

    # ── CLI HELP — each new subcommand parses ────────────────────────────
    cli_subcommands = [
        ("data", "upsert"),
        ("data", "import"),
        ("data", "bulk", "update"),
        ("data", "bulk", "upsert"),
        ("data", "bulk", "delete"),
        ("data", "bulk", "import"),
        ("org", "assign", "permset"),
        ("org", "assign", "permsetlicense"),
        ("package", "install"),
        ("package", "uninstall"),
        ("deploy-quick",),
        ("deploy-resume",),
        ("deploy-abort",),
        ("post-deploy", "poll"),
        ("post-deploy", "daemon"),
        ("post-deploy", "show"),
    ]
    for cmd_parts in cli_subcommands:
        cmd_str = " ".join(cmd_parts)
        code, out, err = _run_cli(list(cmd_parts) + ["--help"])
        # --help exits 0 with usage line
        check(f"F-CLI-{cmd_str} --help exits 0", code == 0)
        check(f"F-CLI-{cmd_str} --help has usage line",
              "usage:" in out.lower() or "usage:" in err.lower())

    # ── REVERT CAPABILITIES — new op_types ───────────────────────────────
    from jsc_revert.revert_capabilities import compute_revertibility

    # v7.17.0 Codex-R1-P1-08 closure: op_types added in Phase I.4-extended-2
    # whose planner is not yet implemented are classified REVERTIBLE_FALSE with
    # documented manual_recovery_path. Re-promote individually as planner cases
    # land in v7.17.1+.

    # data_record_upsert: REVERTIBLE_FALSE (forensic-only) + recovery path
    r = compute_revertibility("data_record_upsert", {})
    check("F-RC-upsert is False (forensic-only)", r["automatic_revertible"] is False)
    check("F-RC-upsert has manual_recovery_path",
          r["manual_recovery_path"] and "before_record_id" in r["manual_recovery_path"])

    # data_bulk_delete: BOTH soft + hard → False in v7.17.0 (no planner).
    # Captured forensic CSV enables manual revert; hard-delete keeps the
    # original hard-delete-specific recovery prose.
    r_soft = compute_revertibility("data_bulk_delete", {"delete_mode": "soft"})
    r_hard = compute_revertibility("data_bulk_delete", {"delete_mode": "hard"})
    check("F-RC-bulk-delete-soft is False (forensic-only)",
          r_soft["automatic_revertible"] is False)
    check("F-RC-bulk-delete-soft has manual_recovery_path",
          r_soft["manual_recovery_path"] and "before_csv" in r_soft["manual_recovery_path"])
    check("F-RC-bulk-delete-hard is False", r_hard["automatic_revertible"] is False)
    check("F-RC-bulk-delete-hard has manual_recovery_path",
          r_hard["manual_recovery_path"] and "before_csv" in r_hard["manual_recovery_path"])

    # org_assign_permset / permsetlicense → False (forensic-only); captured
    # PSA/PSL ids enable manual delete-by-id.
    r = compute_revertibility("org_assign_permset", {})
    check("F-RC-permset is False (forensic-only)", r["automatic_revertible"] is False)
    check("F-RC-permset has PSA-id recovery",
          r["manual_recovery_path"] and "PermissionSetAssignment" in r["manual_recovery_path"])
    r = compute_revertibility("org_assign_permsetlicense", {})
    check("F-RC-permsetlicense is False (forensic-only)",
          r["automatic_revertible"] is False)

    # deploy_quick_promote: False (no metadata-before bytes captured in v7.17.0)
    r = compute_revertibility("deploy_quick_promote", {})
    check("F-RC-deploy-quick is False (no metadata-before)",
          r["automatic_revertible"] is False)
    check("F-RC-deploy-quick has manual_recovery_path",
          r["manual_recovery_path"] and "earlier snapshot" in r["manual_recovery_path"])

    # package_install + package_uninstall → False (non-revertible)
    r = compute_revertibility("package_install", {})
    check("F-RC-package-install is False", r["automatic_revertible"] is False)
    check("F-RC-package-install has manual_recovery_path",
          r["manual_recovery_path"] and "uninstall" in r["manual_recovery_path"].lower())

    r = compute_revertibility("package_uninstall", {})
    check("F-RC-package-uninstall is False", r["automatic_revertible"] is False)

    # ── POST-DEPLOY POLLING ──────────────────────────────────────────────
    from jsc_revert import post_deploy_polling as pdp

    # poll_once on empty queue → all zeros, no crash
    with tempfile.TemporaryDirectory() as tmp:
        qd = Path(tmp) / "queue"
        stats = pdp.poll_once(qd)
        check("F-PDP-1 poll_once on missing queue dir returns zeros",
              stats == {"polled": 0, "completed": 0, "partial": 0, "failed": 0, "deferred": 0, "exhausted": 0})

    # enqueue + show via internal _load_entries
    with tempfile.TemporaryDirectory() as tmp:
        qd = Path(tmp) / "queue"
        eid = pdp.enqueue(
            snapshot_id="abc123",
            org_id_short="00D000000000001",
            alias="sf-test",
            operation_type="deploy_metadata",
            job_id="0Af000000000001AAA",
            sf_report_command=["echo", "{}"],
            queue_dir=qd,
        )
        check("F-PDP-2 enqueue returns queue_entry_id (16 hex)",
              isinstance(eid, str) and len(eid) == 16)
        entries = pdp._load_entries(qd / "queue.jsonl")
        check("F-PDP-2b enqueue persists 1 entry", len(entries) == 1)
        check("F-PDP-2c entry has next_poll_at in future",
              entries[0]["next_poll_at"] >= pdp._iso_now())

    # poll_once with a deferred entry doesn't process it yet
    with tempfile.TemporaryDirectory() as tmp:
        qd = Path(tmp) / "queue"
        pdp.enqueue(
            snapshot_id="xyz789",
            org_id_short="00D000000000001",
            alias="sf-test",
            operation_type="deploy_metadata",
            job_id="0Af000000000002AAA",
            sf_report_command=["echo", "{}"],
            queue_dir=qd,
        )
        stats = pdp.poll_once(qd)
        check("F-PDP-3 deferred entry stays deferred (polled=1, deferred=1)",
              stats["polled"] == 1 and stats["deferred"] == 1)

    # exp_backoff bounds
    check("F-PDP-4 exp_backoff(1) == 60", pdp._exp_backoff(1) == 60)
    check("F-PDP-4b exp_backoff(2) == 120", pdp._exp_backoff(2) == 120)
    check("F-PDP-4c exp_backoff(20) capped at 1800",
          pdp._exp_backoff(20) == 1800)

    # Iso-format check
    iso = pdp._iso_now()
    check("F-PDP-5 _iso_now format (UTC Z)",
          len(iso) == 20 and iso.endswith("Z") and iso[10] == "T")

    # post-deploy poll CLI runs cleanly on empty queue
    with tempfile.TemporaryDirectory() as tmp:
        code, out, err = _run_cli(["post-deploy", "poll", "--queue-dir", str(Path(tmp) / "queue")])
        check("F-PDP-6 post-deploy poll on empty queue exits 0", code == 0)
        check("F-PDP-6b output is JSON stats", "polled" in out)

    # Packaging supplies the console script; a module invocation works without shell setup.
    from jsc_revert.revert_planner import _jsc_command
    command = _jsc_command()
    check("F-SH-1 wrapper invocation uses current environment", bool(command) and Path(command[0]).is_absolute())
    proc = subprocess.run(command + ["--help"], text=True, capture_output=True, timeout=10)
    check("F-SH-1b planned wrapper entry point is runnable", proc.returncode == 0 and "usage:" in proc.stdout.lower())

    # ── BULK CLASSIFIER (v7.17.0 Codex-R1-P1-04) ─────────────────────────
    from jsc_revert.wrappers import _common as common

    # JobComplete + 0 failed → complete
    sn, ex, jid = common.classify_bulk_status(
        {"result": {"jobInfo": {"id": "750000000000001AAA", "state": "JobComplete"},
                    "numberRecordsFailed": 0, "numberRecordsProcessed": 10}}, 0)
    check("F-BULK-1 JobComplete=complete", sn == "complete")
    check("F-BULK-1b extracts jobInfo.id", jid == "750000000000001AAA")
    check("F-BULK-1c wrapper_exit = 0", ex == 0)

    # JobComplete + N failed → applied_partial
    sn, ex, jid = common.classify_bulk_status(
        {"result": {"jobInfo": {"id": "750000000000001AAA", "state": "JobComplete"},
                    "numberRecordsFailed": 5, "numberRecordsProcessed": 10}}, 0)
    check("F-BULK-2 JobComplete with failures=applied_partial",
          sn == "applied_partial")

    # InProgress → pending_finalize_required
    sn, ex, jid = common.classify_bulk_status(
        {"result": {"jobInfo": {"id": "750000000000002AAA", "state": "InProgress"}}}, 0)
    check("F-BULK-3 InProgress=pending_finalize_required",
          sn == "pending_finalize_required")
    check("F-BULK-3b extracts jobInfo.id from InProgress", jid == "750000000000002AAA")

    # Failed → failed
    sn, ex, jid = common.classify_bulk_status(
        {"result": {"jobInfo": {"id": "750000000000001AAA", "state": "Failed", "numberRecordsProcessed": 0, "numberRecordsFailed": 0}}}, 1)
    check("F-BULK-4 Failed state=failed", sn == "failed")

    # Top-level jobId path (data update bulk)
    sn, ex, jid = common.classify_bulk_status(
        {"result": {"jobId": "750000000000003AAA", "status": "JobComplete", "processedRecords": 3, "failedRecords": 0}}, 0)
    check("F-BULK-5 top-level jobId classified",
          sn == "complete" and jid == "750000000000003AAA")

    # Missing JSON never establishes completion
    sn, ex, jid = common.classify_bulk_status(None, 0)
    check("F-BULK-6 None+exit0=partial", sn == "partial" and jid is None)

    # Missing JSON plus a nonzero exit still cannot establish the job outcome
    sn, ex, jid = common.classify_bulk_status(None, 1)
    check("F-BULK-7 None+exit1=partial", sn == "partial")

    # Maintained leases support long operations without an environment opt-in.
    # Under threshold → 0
    rc = common.assert_lock_safe_or_opt_in(60)
    check("F-LOCK-1 short timeout passes", rc == 0)
    # A normal long timeout is valid with or without the obsolete environment.
    saved = os.environ.pop("JSC_REVERT_LONG_OP_OK", None)
    try:
        rc = common.assert_lock_safe_or_opt_in(2000)
        check("F-LOCK-2 long timeout works without opt-in", rc == 0)
        # Over threshold with opt-in → 0
        os.environ["JSC_REVERT_LONG_OP_OK"] = "1"
        rc = common.assert_lock_safe_or_opt_in(2000)
        check("F-LOCK-3 long timeout passes with opt-in", rc == 0)
    finally:
        os.environ.pop("JSC_REVERT_LONG_OP_OK", None)
        if saved:
            os.environ["JSC_REVERT_LONG_OP_OK"] = saved

    # ── MANIFEST v7.17.0 SCHEMA EXTENSIONS ───────────────────────────────
    from jsc_revert import manifest as mf
    check("F-MF-1 polling_exhausted in VALID_SNAPSHOT_STATUS",
          "polling_exhausted" in mf.VALID_SNAPSHOT_STATUS)
    check("F-MF-2 deploy_quick_promote in VALID_OPERATION_TYPES",
          "deploy_quick_promote" in mf.VALID_OPERATION_TYPES)

    # ── PLANNER-SUPPORTED OP_TYPES (v7.17.0 honesty contract) ────────────
    from jsc_revert.revert_capabilities import PLANNER_SUPPORTED_OP_TYPES
    check("F-RC-planner-includes-deploy_metadata",
          "deploy_metadata" in PLANNER_SUPPORTED_OP_TYPES)
    check("F-RC-planner-excludes-data_bulk_update",
          "data_bulk_update" not in PLANNER_SUPPORTED_OP_TYPES)
    check("F-RC-planner-excludes-org_assign_permset",
          "org_assign_permset" not in PLANNER_SUPPORTED_OP_TYPES)
    check("F-RC-planner-excludes-revert (R2-P2-02)",
          "revert" not in PLANNER_SUPPORTED_OP_TYPES)
    # B4: soft data_record_delete now HAS a planner case (`jsc data undelete`),
    # so it is INCLUDED in PLANNER_SUPPORTED_OP_TYPES (was excluded under the
    # R2-D1 deferred-helper state). The soft/hard split is enforced by the
    # planner's delete_mode gate + compute_revertibility special-case block.
    check("F-RC-planner-includes-data_record_delete (B4 undelete case)",
          "data_record_delete" in PLANNER_SUPPORTED_OP_TYPES)

    # ── _poll_one bulk/package classifier (R2-P1-01) ─────────────────────
    # Each successful polling fixture has a private capture destination. All
    # report responses are mocked, including exact-job read-only fallbacks.
    def _make_entry(op_type, json_payload):
        job_id = ("750000000000001AAA" if op_type.startswith("data_bulk") else
                  "0HU000000000001AAA" if op_type.startswith("package") else "0Af000000000001AAA")
        return {
            "operation_type": op_type,
            "sf_report_command": ["printf", "%s", json_payload],
            "queue_entry_id": "test", "snapshot_id": "test",
            "org_id_short": "00D000000000001", "alias": "sf-test", "job_id": job_id,
        }

    def _poll_fixture(entry):
        with tempfile.TemporaryDirectory() as tmp:
            snap_dir = Path(tmp).resolve()
            payload = entry["sf_report_command"][-1]
            def report(command, **kwargs):
                assert Path(kwargs["cwd"]) == snap_dir
                if command == entry["sf_report_command"]:
                    return subprocess.CompletedProcess(command, 0, payload, "")
                # InProgress can ask for counts. Never submit another job.
                assert ((command[:4] == ["sf", "data", "bulk", "results"] and
                         command[command.index("--job-id") + 1] == entry["job_id"]) or
                        (command[:4] == ["sf", "api", "request", "rest"] and
                         command[4].endswith('/' + entry["job_id"]) and
                         command[command.index("--method") + 1] == "GET"))
                assert command[command.index("--target-org") + 1] == entry["alias"]
                return subprocess.CompletedProcess(command, 1, '{"status":1}', "offline report unavailable")
            with patch.object(mf, "load_by_id", return_value=(snap_dir, {})), \
                    patch.object(subprocess, "run", side_effect=report):
                result = pdp._poll_one(entry)
            observations = entry.get("observation_paths", [])
            check(f"F-PDP-EVIDENCE {entry['operation_type']} stores the report before classifying",
                  bool(observations) and json.loads(Path(observations[0]).read_text(encoding="utf-8"))["stdout"] == payload)
            return result

    missing = _make_entry("deploy_metadata", '{"result":{"status":"Succeeded"}}')
    with patch.object(mf, "load_by_id", side_effect=FileNotFoundError), \
            patch.object(subprocess, "run", side_effect=AssertionError("report must not launch")) as report:
        result = pdp._poll_one(missing)
        check("F-PDP-MISSING absent capture remains pending without launching a report",
              result == "pending" and report.call_count == 0)

    # Bulk JobComplete → complete (via jobInfo.state)
    res = _poll_fixture(_make_entry("data_bulk_update",
        '{"result":{"jobInfo":{"id":"750000000000001AAA","state":"JobComplete","numberRecordsProcessed":3,"numberRecordsFailed":0}}}'))
    check("F-PDP-BULK-1 bulk JobComplete=complete", res == "complete")
    # Bulk InProgress → pending
    res = _poll_fixture(_make_entry("data_bulk_delete",
        '{"result":{"jobInfo":{"id":"750000000000001AAA","state":"InProgress"}}}'))
    check("F-PDP-BULK-2 bulk InProgress=pending", res == "pending")
    # Bulk Aborted → failed
    res = _poll_fixture(_make_entry("data_bulk_import",
        '{"result":{"jobInfo":{"id":"750000000000001AAA","state":"Aborted","numberRecordsProcessed":0,"numberRecordsFailed":0}}}'))
    check("F-PDP-BULK-3 bulk Aborted=failed", res == "failed")
    # Package IN_PROGRESS → pending
    res = _poll_fixture(_make_entry("package_install",
        '{"result":{"id":"0HU000000000001AAA","Status":"IN_PROGRESS"}}'))
    check("F-PDP-PKG-1 package IN_PROGRESS=pending", res == "pending")
    # Package SUCCESS → complete
    res = _poll_fixture(_make_entry("package_install",
        '{"result":{"id":"0HU000000000001AAA","Status":"SUCCESS"}}'))
    check("F-PDP-PKG-2 package SUCCESS=complete", res == "complete")
    # Codex-R3-P1-01: package_uninstall uses title-case Success → complete
    res = _poll_fixture(_make_entry("package_uninstall",
        '{"result":{"id":"0HU000000000001AAA","Status":"Success"}}'))
    check("F-PDP-PKG-3 package_uninstall Status=Success → complete",
          res == "complete")
    # package_uninstall InProgress → pending
    res = _poll_fixture(_make_entry("package_uninstall",
        '{"result":{"id":"0HU000000000001AAA","Status":"InProgress"}}'))
    check("F-PDP-PKG-4 package_uninstall InProgress → pending",
          res == "pending")
    # package_uninstall Queued → pending
    res = _poll_fixture(_make_entry("package_uninstall",
        '{"result":{"id":"0HU000000000001AAA","Status":"Queued"}}'))
    check("F-PDP-PKG-5 package_uninstall Queued → pending",
          res == "pending")

    # Codex-R4-P2-01: full queue+manifest poll_once regression for
    # package_uninstall — closes the test-coverage gap for the R3 case-fix
    with tempfile.TemporaryDirectory() as full_tmp:
        full_root = Path(full_tmp).resolve()
        snap_dir = full_root / "00D000000000001-sf-uninstall-test" / "snap-r4-pdp"
        snap_dir.mkdir(parents=True)
        # Minimal manifest in pending_finalize_required state
        manifest = {
            "schema_version": 4,
            "snapshot_id": "snap-r4-pdp",
            "operation_type": "package_uninstall",
            "wrapper_command": "jsc package uninstall (test)",
            "org": {
                "org_id_18": "00D000000000001AAA",
                "org_id_short": "00D000000000001-sf-uninstall-test",
                "alias": "sf-uninstall-test",
            },
            "snapshot_status": "pending_finalize_required",
            "payload": {"package": "04t000000000001AAA", "uninstall_request_id": "0HU000000000001AAA"},
            "phases": {
                "pre_snapshot": {"status": "complete"},
                "underlying_command": {"status": "pending_finalize_required"},
                "post_finalize": {"status": "complete"},
            },
            "revert_capabilities": {"automatic_revertible": False},
        }
        (snap_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        # Enqueue
        queue_dir = full_root / "_polling_queue"
        qe_id = pdp.enqueue(
            snapshot_id="snap-r4-pdp",
            org_id_short="00D000000000001-sf-uninstall-test",
            alias="sf-uninstall-test",
            operation_type="package_uninstall",
            job_id="0HU000000000001AAA",
            sf_report_command=["printf", "%s", '{"result":{"id":"0HU000000000001AAA","Status":"Success"}}'],
            queue_dir=queue_dir,
        )
        # Backdate next_poll_at so poll_once actually polls (not defers).
        queue_file = queue_dir / "queue.jsonl"
        entries_raw = [json.loads(l) for l in queue_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        entries_raw[-1]["next_poll_at"] = "2020-01-01T00:00:00Z"
        queue_file.write_text("\n".join(json.dumps(e) for e in entries_raw) + "\n", encoding="utf-8")
        # Monkeypatch the manifest module's snapshot resolver + save to use our
        # temp tree (skip envelope validation — we're testing the polling flow,
        # not the manifest schema validator).
        from jsc_revert import manifest as mf
        saved_load = mf.load_by_id
        saved_save = mf.save
        manifest_state = {"m": dict(manifest)}
        mf.load_by_id = lambda org_id_short, alias, snap_id: (
            snap_dir, dict(manifest_state["m"]))
        def fake_save(d, m):
            manifest_state["m"] = dict(m)
            (snap_dir / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
        mf.save = fake_save
        try:
            stats = pdp.poll_once(queue_dir)
            check("F-PDP-FULL-1 poll_once completed=1 for package_uninstall Success",
                  stats.get("completed") == 1)
            # Check the queue entry is tombstoned complete
            entries = pdp._load_entries(queue_dir / "queue.jsonl")
            target = next((e for e in entries if e["queue_entry_id"] == qe_id), None)
            check("F-PDP-FULL-2 queue entry status=complete after poll_once",
                  target and target.get("status") == "complete")
            check("F-PDP-FULL-3 manifest snapshot_status=complete after poll_once",
                  manifest_state["m"].get("snapshot_status") == "complete")
            check("F-PDP-FULL-4 manifest has post_deploy_polling.final_status=complete",
                  manifest_state["m"].get("post_deploy_polling", {}).get("final_status") == "complete")
        finally:
            mf.load_by_id = saved_load
            mf.save = saved_save
    # Deploy Succeeded → complete (regression check)
    res = _poll_fixture(_make_entry("deploy_metadata",
        '{"result":{"id":"0Af000000000001AAA","status":"Succeeded","done":true,"success":true,"numberComponentErrors":0,"numberTestErrors":0}}'))
    check("F-PDP-DEPLOY-1 deploy Succeeded=complete (no regression)",
          res == "complete")

    # ── data_bulk_upsert tagged pre-query (R2-P1-03) ─────────────────────
    from jsc_revert.wrappers import data_bulk_upsert as dbu

    # Monkeypatch run_sf_subprocess to return a query failure
    saved_run = dbu.c.run_sf_subprocess
    try:
        dbu.c.run_sf_subprocess = lambda cmd, timeout_seconds=180, cwd=None: (1, "{bad", "query failed")
        status, count = dbu._query_by_ext_ids_tagged(
            "fake", "Account", "Ext__c", ["A", "B"], ["Id", "Ext__c"],
            Path("/tmp/jsc-test-bulk-upsert-fail.csv"),
        )
        check("F-BU-PRE-1 bulk_upsert query-fail returns ('error', 0)",
              status == "error" and count == 0)

        # Clean no-match
        dbu.c.run_sf_subprocess = lambda cmd, timeout_seconds=180, cwd=None: (0,
            '{"result":{"done":true,"totalSize":0,"records":[]}}', "")
        status, count = dbu._query_by_ext_ids_tagged(
            "fake", "Account", "Ext__c", ["A", "B"], ["Id", "Ext__c"],
            Path("/tmp/jsc-test-bulk-upsert-empty.csv"),
        )
        check("F-BU-PRE-2 bulk_upsert clean-no-match returns ('no_match', 0)",
              status == "no_match" and count == 0)

        # OK with matching rows
        dbu.c.run_sf_subprocess = lambda cmd, timeout_seconds=180, cwd=None: (0,
            '{"result":{"done":true,"totalSize":1,"records":[{"Id":"001000000000001AAA","Ext__c":"A"}]}}', "")
        out_csv = Path(tempfile.mkdtemp()) / "bu_ok.csv"
        status, count = dbu._query_by_ext_ids_tagged(
            "fake", "Account", "Ext__c", ["A"], ["Id", "Ext__c"], out_csv,
        )
        check("F-BU-PRE-3 bulk_upsert success returns ('ok', N) + CSV written",
              status == "ok" and count == 1 and out_csv.exists())
    finally:
        dbu.c.run_sf_subprocess = saved_run

    # ── successFilePath shape (R2-P1-02) ─────────────────────────────────
    # Runtime check: monkeypatch run_sf_subprocess to return JSON with
    # successFilePath. Wrapper should read it and write the target out_path.
    tmpdir = Path(tempfile.mkdtemp())
    src_csv = tmpdir / "src-success.csv"
    src_csv.write_text("sf__Id,Ext__c,sf__Created\n001000000000001AAA,A,true\n", encoding="utf-8")
    saved_run2 = dbu.c.run_sf_subprocess
    try:
        dbu.c.run_sf_subprocess = lambda cmd, timeout_seconds=120, cwd=None: (
            0, json.dumps({"result": {"successFilePath": str(src_csv)}}), "")
        out = tmpdir / "out-success.csv"
        ok = dbu._fetch_bulk_results("fake", "750000000000001AAA", out)
        check("F-BU-RES-1 _fetch_bulk_results reads successFilePath + copies",
              ok and out.exists() and out.read_text(encoding="utf-8") == src_csv.read_text(encoding="utf-8"))
        # Old shape: filesWritten — should NOT be picked up
        dbu.c.run_sf_subprocess = lambda cmd, timeout_seconds=120, cwd=None: (
            0, json.dumps({"result": {"filesWritten": [str(src_csv)]}}), "")
        out2 = tmpdir / "out-no.csv"
        ok = dbu._fetch_bulk_results("fake", "750000000000001AAA", out2)
        check("F-BU-RES-2 filesWritten alone (legacy) does NOT match — returns False",
              ok is False)
    finally:
        dbu.c.run_sf_subprocess = saved_run2

    # Same shape check for data_bulk_import
    from jsc_revert.wrappers import data_bulk_import as dbi
    saved_run3 = dbi.c.run_sf_subprocess
    try:
        dbi.c.run_sf_subprocess = lambda cmd, timeout_seconds=120, cwd=None: (
            0, json.dumps({"result": {"successFilePath": str(src_csv)}}), "")
        out3 = tmpdir / "out-import.csv"
        ok = dbi._fetch_bulk_results("fake", "750000000000002AAA", out3)
        check("F-BI-RES-1 data_bulk_import._fetch_bulk_results reads successFilePath",
              ok and out3.exists())
    finally:
        dbi.c.run_sf_subprocess = saved_run3

    for label, passed in tests:
        print(f"{'PASS' if passed else 'FAIL'}: {label}")
    if failures:
        print(f"\nphase_i4_extended_2 self-test FAILED: {failures} fixture(s)", file=sys.stderr)
        return 1
    print(f"\nphase_i4_extended_2 self-test PASSED ({len(tests)} fixtures)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
