#!/usr/bin/env python3
"""Integration tests for jsc_revert.cli — Phase I.4-extended D.

Tests cli + intent_marker + manifest + bundle integration WITHOUT requiring
a live Salesforce org. Uses tempdir-isolated JSC_REVERT_DIR + token path.
"""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

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

    # ── F-CLI-1: --help works ─────────────────────────────────────────────
    code, out, err = _run_cli(["--help"])
    check("F-CLI-1 --help exits 0", code == 0)
    check("F-CLI-1b --help mentions revert-token", "revert-token" in out)
    check("F-CLI-1c --help mentions revert", "revert" in out and "snapshot" in out)

    # ── F-CLI-2: revert-token show with no token → 'No token present' ────
    with tempfile.TemporaryDirectory() as tmpd:
        token_path = Path(tmpd) / ".token.json"
        env = {"JSC_ROOT": tmpd}  # redirect default token path to tempdir
        code, out, err = _run_cli(["revert-token", "show"], env)
        check("F-CLI-2 revert-token show with no token → exit 0", code == 0)
        check("F-CLI-2b revert-token show prints 'No token present'", "No token present" in out)

    # ── F-CLI-3: revert-token revoke with no token → 'No token to revoke' ─
    with tempfile.TemporaryDirectory() as tmpd:
        env = {"JSC_ROOT": tmpd}
        code, out, err = _run_cli(["revert-token", "revoke"], env)
        check("F-CLI-3 revert-token revoke with no token → exit 0", code == 0)
        check("F-CLI-3b prints 'No token to revoke'", "No token" in out)

    # ── F-CLI-4: revert-token grant with bad reason → exit 1 + diagnostic ─
    # We can't actually mint a token without a resolvable org. But we can
    # exercise argument parsing failures.
    code, out, err = _run_cli([
        "revert-token", "grant",
        "--org", "no-such-org-xyz-deadbeef",
        "--reason", "JIRA-1: x",  # body too short
    ])
    # Either fails at org-resolve (most likely) or at reason validation
    check("F-CLI-4 grant with bad org/reason exits non-zero", code != 0)

    # ── F-CLI-5: subcommand help works ────────────────────────────────────
    for sub in ["deploy", "apex run", "data update", "data create", "data delete",
                "data undelete",
                "revert show", "revert preview", "revert exec", "revert discard"]:
        code, out, err = _run_cli(sub.split() + ["--help"])
        check(f"F-CLI-5 `jsc {sub} --help` exits 0", code == 0)

    # ── F-CLI-6: revert show against unresolvable org → exit 1 + diagnostic ─
    code, out, err = _run_cli(["revert", "show", "--org", "no-such-org-xyz-deadbeef"])
    check("F-CLI-6 revert show with unresolvable org exits 1", code == 1)
    check("F-CLI-6b error mentions cannot resolve", "cannot resolve" in err.lower())

    # ── F-CLI-7: e2e mint via intent_marker → cli show → cli revoke ──────
    # This verifies cli + intent_marker integrate (using temp client workspace so we don't
    # touch operator's real token).
    with tempfile.TemporaryDirectory() as tmpd:
        env_temp_home = {"JSC_ROOT": tmpd}
        # Mint via intent_marker directly (skipping org resolution)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from jsc_revert.intent_marker import mint, DEFAULT_TOKEN_PATH
        token_path_in_tmpd = Path(tmpd) / "state" / "legacy-tokens" / ".deploy_intent_token.json"
        mint(
            operation_type="manual-bypass",
            org_id_18="00DPP0000004XYZAB1",
            command="sf project deploy start --target-org sf-test",
            reason="INC-9999: integration test mint via Python API",
            expiry_seconds=600,
            target_path=token_path_in_tmpd,
        )
        check("F-CLI-7a token file created in temp client workspace", token_path_in_tmpd.exists())

        code, out, err = _run_cli(["revert-token", "show"], env_temp_home)
        check("F-CLI-7b cli show finds token", code == 0 and "schema_version" in out)
        check("F-CLI-7c token has expected fields",
              "operation_type" in out and "manual-bypass" in out)

        code, out, err = _run_cli(["revert-token", "revoke"], env_temp_home)
        check("F-CLI-7d cli revoke succeeds", code == 0 and "revoked" in out.lower())
        check("F-CLI-7e token file gone after revoke", not token_path_in_tmpd.exists())

    # ── F-CLI-8: executor->CLI seam — every revert command the executor builds
    # MUST parse under the CLI. Regression for REVERT-1 / TEST-1 (audit 2026-06-09):
    # before the fix, data_record_update/create revert commands died with argparse
    # "unrecognized arguments" because the data subparsers lacked the forensic flags. ─
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from jsc_revert.cli import build_parser
    from jsc_revert.revert_planner import build_revert_command
    from jsc_revert.revert_executor import _append_forensic_chain

    def _seam_parses(manifest: dict, snap_dir: Path):
        cmd = build_revert_command(manifest, snap_dir)
        if cmd is None:
            return False, None
        full = _append_forensic_chain(cmd, manifest["snapshot_id"], "revert-of test")
        try:
            build_parser().parse_args(full[3:] if full[1:3] == ["-m", "jsc_revert.cli"] else full[1:])
            return True, full
        except SystemExit:
            return False, full

    ok_upd, _ = _seam_parses({
        "operation_type": "data_record_update", "snapshot_id": "parentUPD",
        "org": {"alias": "sf-test"},
        "payload": {"object_api_name": "Account", "record_id": "001000000000001AAA",
                    "fields_updated": ["Name"],
                    "before_row": {"Id": "001000000000001AAA", "Name": "Old Name"}},
    }, Path("/nonexistent"))
    check("F-CLI-8a data_record_update revert command parses under CLI", ok_upd)

    ok_crt, _ = _seam_parses({
        "operation_type": "data_record_create", "snapshot_id": "parentCRT",
        "org": {"alias": "sf-test"},
        "payload": {"object_api_name": "Account", "record_id": "001000000000002AAA"},
    }, Path("/nonexistent"))
    check("F-CLI-8b data_record_create revert command parses under CLI", ok_crt)

    with tempfile.TemporaryDirectory() as td:
        # Canonical paths match state_dir's real-root contract on macOS. A
        # source body alone is not a capture: retain its required companion,
        # matching selector and checksum inventory as the real wrapper does.
        snap_dir = Path(td).resolve()
        mb = snap_dir / "metadata-before"
        (mb / "classes").mkdir(parents=True)
        contents = {
            "Foo.cls": "public class Foo {}\n",
            "Foo.cls-meta.xml": '<ApexClass xmlns="http://soap.sforce.com/2006/04/metadata"><apiVersion>67.0</apiVersion><status>Active</status></ApexClass>',
        }
        captured = []
        for name, content in contents.items():
            path = mb / "classes" / name
            path.write_text(content)
            captured.append({"type": "ApexClass", "fullName": "Foo",
                             "before_state": "present", "filePath": str(path),
                             "before_checksum": hashlib.sha256(path.read_bytes()).hexdigest()})
        ok_dep, full_dep = _seam_parses({
            "operation_type": "deploy_metadata", "snapshot_id": "parentDEP",
            "org": {"alias": "sf-test"},
            "payload": {"selectors": {"metadata_args": ["ApexClass:Foo"]}, "files": captured},
        }, snap_dir)
        check("F-CLI-8c deploy_metadata revert command parses under CLI", ok_dep)
        check("F-CLI-8d deploy revert command not double-flagged (idempotent append)",
              full_dep is not None and full_dep.count("--operation-type") == 1)

    # Regression for R3-P0-02 itself: an EMPTY metadata-before must yield no
    # plan at all, rather than a command that deploys nothing.
    with tempfile.TemporaryDirectory() as td2:
        snap_dir = Path(td2).resolve()
        (snap_dir / "metadata-before").mkdir()
        from revert.jsc_revert.revert_planner import build_revert_command
        check("F-CLI-8e empty metadata-before yields NO revert plan",
              build_revert_command({
                  "operation_type": "deploy_metadata", "snapshot_id": "emptyDEP",
                  "org": {"alias": "sf-test"}, "payload": {},
              }, snap_dir) is None)

    # B4: soft data_record_delete revert → `jsc data undelete` command must
    # parse under the CLI (regression for the dedicated undelete subparser).
    ok_undel, full_undel = _seam_parses({
        "operation_type": "data_record_delete", "snapshot_id": "parentDEL",
        "org": {"alias": "sf-test"},
        "payload": {"object_api_name": "Contact", "record_id": "003000000000001AAA",
                    "before_row": {"Id": "003000000000001AAA", "FirstName": "Jane"},
                    "delete_mode": "soft"},
    }, Path("/nonexistent"))
    check("F-CLI-8e soft-delete undelete revert command parses under CLI", ok_undel)
    check("F-CLI-8f undelete revert command not double-flagged (idempotent append)",
          full_undel is not None and full_undel.count("--operation-type") == 1)

    # Hard delete → no plan (None) → seam returns False (nothing to parse).
    ok_hard, full_hard = _seam_parses({
        "operation_type": "data_record_delete", "snapshot_id": "parentDELh",
        "org": {"alias": "sf-test"},
        "payload": {"object_api_name": "Contact", "record_id": "003000000000002AAA",
                    "before_row": {"Id": "003000000000002AAA"}, "delete_mode": "hard"},
    }, Path("/nonexistent"))
    check("F-CLI-8g hard-delete builds no revert command (None)",
          ok_hard is False and full_hard is None)

    for label, passed in tests:
        print(f"{'PASS' if passed else 'FAIL'}: {label}")

    if failures:
        print(f"\ncli_integration self-test FAILED: {failures} fixture(s)", file=sys.stderr)
        return 1
    print(f"\ncli_integration self-test PASSED ({len(tests)} fixtures)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
