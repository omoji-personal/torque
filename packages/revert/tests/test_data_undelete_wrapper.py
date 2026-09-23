#!/usr/bin/env python3
"""Wrapper self-control test for `jsc data undelete` (B4).

OFFLINE only — mocks org resolution, the per-org lock, and the underlying
`sf apex run` subprocess. Asserts the wrapper:
  (a) parses through the CLI arg-parser (the dedicated `data undelete` subparser),
  (b) takes its OWN pre-snapshot + acquires the org-lock + writes a LINKED child
      snapshot (parent_snapshot_id set when invoked as a revert),
  (c) the WRITTEN snapshot manifest contains op_type data_undelete and the
      soft-delete snapshot path records delete_mode:"soft".

Wrappers are EXEMPT from revert_warning routing (they self-snapshot), so this
test deliberately does NOT assert revert_warning behavior — it targets the
wrapper's OWN snapshot/lock safety path.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jsc_revert import org_detect, org_sequence, bundle, manifest as mf
from jsc_revert.cli import build_parser
from jsc_revert.wrappers import _common as common
from jsc_revert.wrappers import data_undelete


def _fake_org():
    return org_detect.OrgInfo(
        alias="sf-undelete-test",
        org_id_18="00DPP0000004XYZAB1",
        org_id_short="00DPP0000004XYZ",
        is_sandbox=True,
        instance_url="https://example--sbx.sandbox.my.salesforce.com",
        login_url="https://test.salesforce.com",
        detected_org_type="sandbox",
    )


def main() -> int:  # noqa: C901
    failures = 0
    tests = []

    def check(label: str, condition: bool):
        nonlocal failures
        tests.append((label, condition))
        if not condition:
            failures += 1

    # ── (a) CLI arg-parser path ──────────────────────────────────────────
    parser = build_parser()
    ns = parser.parse_args([
        "data", "undelete",
        "-o", "sf-undelete-test",
        "--sobject", "Contact",
        "--record-id", "003xx0000099AAA",
        "--parent-snapshot-id", "parentDEL",
        "--operation-type", "revert",
        "--invoking-intent", "revert-of parentDEL",
    ])
    check("F-DU-1 `data undelete` parses under the CLI", ns.func is not None)
    check("F-DU-1b parsed record_id present", ns.record_id == "003xx0000099AAA")
    check("F-DU-1c parsed sobject present", ns.sobject == "Contact")
    check("F-DU-1d revert-chain args parsed (parent + op-type)",
          ns.parent_snapshot_id == "parentDEL" and ns.operation_type == "revert")

    # ── (b)+(c) run the wrapper with mocks; inspect the WRITTEN manifest ──
    acquired = {"called": False}
    released = {"called": False}

    def fake_acquire_lock(org_id_short, alias, snapshot_id, operation_type, session_id=None):
        acquired["called"] = True
        return {
            "owner_token": "tok-" + snapshot_id, "owner_pid": os.getpid(),
            "owner_hostname": "test", "snapshot_id": snapshot_id,
            "operation_type": operation_type, "org_sequence": 1,
            "acquired_at_iso": "2026-06-13T00:00:00Z",
            "heartbeat_at_iso": "2026-06-13T00:00:00Z",
        }

    def fake_release(org_id_short, alias, owner_token, **kwargs):
        released["called"] = True

    # underlying `sf apex run` → success JSON; the post-finalize `sf data get
    # record` → a row. Distinguish by argv.
    def fake_run_sf_subprocess(cmd, timeout_seconds=600):
        if "apex" in cmd and "run" in cmd:
            return 0, json.dumps({"result": {"success": True, "compiled": True,
                                             "id": "apexExecId"}}), ""
        if "data" in cmd and "get" in cmd:
            return 0, json.dumps({"result": {"Id": "003xx0000099AAA",
                                             "FirstName": "Jane"}}), ""
        return 0, "{}", ""

    saved_resolve = org_detect.resolve_org
    saved_acquire = org_sequence.acquire_lock
    saved_release = org_sequence.release
    saved_run = common.run_sf_subprocess

    with tempfile.TemporaryDirectory() as tmpd:
        os.environ["JSC_REVERT_DIR"] = tmpd
        try:
            org_detect.resolve_org = lambda alias, timeout_seconds=10: _fake_org()
            org_sequence.acquire_lock = fake_acquire_lock
            org_sequence.release = fake_release
            common.run_sf_subprocess = fake_run_sf_subprocess

            rc = data_undelete.run(ns)
            check("F-DU-2 wrapper run() returns EXIT_SUCCESS", rc == common.EXIT_SUCCESS)
            check("F-DU-2b wrapper acquired the org-lock", acquired["called"] is True)
            check("F-DU-2c wrapper released the org-lock (finally)", released["called"] is True)

            # Locate the written manifest.
            org_root = Path(tmpd) / "00DPP0000004XYZ-sf-undelete-test"
            manifests = list(org_root.glob("*/manifest.json"))
            check("F-DU-3 exactly one snapshot bundle written", len(manifests) == 1)
            m = json.loads(manifests[0].read_text(encoding="utf-8"))

            # (c) op_type + delete_mode soft + linked parent.
            check("F-DU-3a manifest operation_type == revert (invoked as revert)",
                  m["operation_type"] == "revert")
            check("F-DU-3b payload.delete_mode == 'soft' (soft-delete snapshot path)",
                  m["payload"]["delete_mode"] == "soft")
            check("F-DU-3c manifest parent_snapshot_id linked",
                  m.get("parent_snapshot_id") == "parentDEL")
            check("F-DU-3d invoking_intent kind == revert-of",
                  m.get("invoking_intent", {}).get("kind") == "revert-of")
            check("F-DU-3e pre_snapshot phase complete",
                  m["phases"]["pre_snapshot"]["status"] == "complete")
            check("F-DU-3f underlying_command phase complete",
                  m["phases"]["underlying_command"]["status"] == "complete")
            check("F-DU-3g snapshot_status complete",
                  m["snapshot_status"] == "complete")
            check("F-DU-3h payload records record_id",
                  m["payload"]["record_id"] == "003xx0000099AAA")

            # The undelete Apex was persisted in the bundle for forensics.
            snap_dir = manifests[0].parent
            apex_file = snap_dir / "undelete_input.apex"
            check("F-DU-4 undelete Apex persisted in bundle", apex_file.exists())
            apex = apex_file.read_text(encoding="utf-8")
            check("F-DU-4b persisted Apex has ALL ROWS inside brackets",
                  "ALL ROWS]" in apex and "[SELECT Id FROM Contact WHERE Id = :rid ALL ROWS]" in apex)
            check("F-DU-4c persisted Apex binds id via :rid (not interpolated in SOQL)",
                  ":rid" in apex and "WHERE Id = '003xx0000099AAA'" not in apex)

        finally:
            org_detect.resolve_org = saved_resolve
            org_sequence.acquire_lock = saved_acquire
            org_sequence.release = saved_release
            common.run_sf_subprocess = saved_run
            os.environ.pop("JSC_REVERT_DIR", None)

    # ── (c, direct-invocation variant) op_type data_undelete when NOT a revert ─
    ns2 = parser.parse_args([
        "data", "undelete", "-o", "sf-undelete-test",
        "--sobject", "Contact", "--record-id", "003xx0000099BBB",
    ])
    check("F-DU-5 default operation_type == data_undelete (non-revert invocation)",
          ns2.operation_type == "data_undelete")

    with tempfile.TemporaryDirectory() as tmpd:
        os.environ["JSC_REVERT_DIR"] = tmpd
        try:
            org_detect.resolve_org = lambda alias, timeout_seconds=10: _fake_org()
            org_sequence.acquire_lock = fake_acquire_lock
            org_sequence.release = fake_release
            common.run_sf_subprocess = fake_run_sf_subprocess
            data_undelete.run(ns2)
            org_root = Path(tmpd) / "00DPP0000004XYZ-sf-undelete-test"
            m = json.loads(next(org_root.glob("*/manifest.json")).read_text(encoding="utf-8"))
            check("F-DU-5b non-revert manifest op_type == data_undelete",
                  m["operation_type"] == "data_undelete")
            check("F-DU-5c non-revert delete_mode still 'soft'",
                  m["payload"]["delete_mode"] == "soft")
        finally:
            org_detect.resolve_org = saved_resolve
            org_sequence.acquire_lock = saved_acquire
            org_sequence.release = saved_release
            common.run_sf_subprocess = saved_run
            os.environ.pop("JSC_REVERT_DIR", None)

    # ── input validation rejects bad inputs BEFORE any lock/snapshot ─────
    ns_bad = parser.parse_args([
        "data", "undelete", "-o", "sf-undelete-test",
        "--sobject", "Contact", "--record-id", "not-a-valid-id",
    ])
    acquired["called"] = False
    with tempfile.TemporaryDirectory() as tmpd:
        os.environ["JSC_REVERT_DIR"] = tmpd
        try:
            org_detect.resolve_org = lambda alias, timeout_seconds=10: _fake_org()
            org_sequence.acquire_lock = fake_acquire_lock
            org_sequence.release = fake_release
            common.run_sf_subprocess = fake_run_sf_subprocess
            rc = data_undelete.run(ns_bad)
            check("F-DU-6 invalid record-id rejected with non-zero exit",
                  rc == common.EXIT_PRESNAP_FAILED_PROD)
            check("F-DU-6b invalid input rejected BEFORE acquiring lock",
                  acquired["called"] is False)
        finally:
            org_detect.resolve_org = saved_resolve
            org_sequence.acquire_lock = saved_acquire
            org_sequence.release = saved_release
            common.run_sf_subprocess = saved_run
            os.environ.pop("JSC_REVERT_DIR", None)

    for label, passed in tests:
        print(f"{'PASS' if passed else 'FAIL'}: {label}")
    if failures:
        print(f"\ndata_undelete_wrapper self-test FAILED: {failures} fixture(s)", file=sys.stderr)
        return 1
    print(f"\ndata_undelete_wrapper self-test PASSED ({len(tests)} fixtures)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
