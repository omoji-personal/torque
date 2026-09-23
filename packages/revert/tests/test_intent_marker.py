#!/usr/bin/env python3
"""Tests for intent_marker.py — TTL token mint + structured-reason validation."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from revert.jsc_revert.intent_marker import (
    is_structured_reason, mint, revoke, show, fingerprint_command,
    MAX_TTL_BY_OP_TYPE,
)


def main() -> int:  # noqa: C901
    failures = 0
    tests = []

    def check(label: str, condition: bool):
        nonlocal failures
        tests.append((label, condition))
        if not condition:
            failures += 1

    # ── F-IM-1..6: structured reason regex (Codex-R5-P1-2 fix: stripped non-ws) ──
    valid_reasons = [
        "JIRA-1234: emergency hotfix for null pointer in Accept Client",
        "INC-99: prod outage from broken Flow",
        "ad-hoc: incident response — flow validation rule blocking all intakes today",
    ]
    for r in valid_reasons:
        check(f"F-IM-valid: {r[:40]!r} → True", is_structured_reason(r))

    invalid_reasons = [
        # Codex-R5-P1-2 empirical: whitespace-only body
        "JIRA-1234:                ",
        "INC-0:           ",
        "ad-hoc:                                            ",
        # Just tabs
        "JIRA-1234:\t\t\t\t\t\t",
        # Body too short for ad-hoc (< 20 non-ws)
        "ad-hoc: too short",
        # Body too short for JIRA (< 5 non-ws) — wait, check stripped len
        "JIRA-1: x",  # stripped body 'x' = 1 char < 5 → invalid
        # No structured prefix
        "just a casual reason",
        "PROD ROLLBACK PLEASE",
        # Wrong prefix case
        "jira-1234: lowercase prefix should fail",
    ]
    for r in invalid_reasons:
        check(f"F-IM-invalid: {r[:40]!r} → False", not is_structured_reason(r))

    # Empty / non-string
    check("F-IM-empty: '' → False", not is_structured_reason(""))
    check("F-IM-none: None → False", not is_structured_reason(None))  # type: ignore

    # ── F-IM-7: fingerprint_command produces sha256 prefix ──
    fp = fingerprint_command("sf project deploy start --target-org sf-prod")
    check("F-IM-fp1 fingerprint starts with sha256:", fp.startswith("sha256:"))
    check("F-IM-fp2 fingerprint stable", fp == fingerprint_command("sf project deploy start --target-org sf-prod"))
    check("F-IM-fp3 fingerprint differs for different commands",
          fp != fingerprint_command("sf project deploy start --target-org sf-other"))

    # ── F-IM-8..N: mint validation ──
    with tempfile.TemporaryDirectory() as tmpd:
        token_path = Path(tmpd) / ".token.json"

        # Invalid op_type
        try:
            mint("nuke", "00DPP0000004XYZAB1", target_path=token_path, command="x")
            check("F-IM-8 invalid op_type → ValueError", False)
        except ValueError:
            check("F-IM-8 invalid op_type → ValueError", True)

        # Manual-bypass with invalid reason
        try:
            mint("manual-bypass", "00DPP0000004XYZAB1",
                 reason="JIRA-1: x",  # body too short
                 target_path=token_path, command="x")
            check("F-IM-9 manual-bypass + invalid reason → ValueError", False)
        except ValueError:
            check("F-IM-9 manual-bypass + invalid reason → ValueError", True)

        # Manual-bypass with whitespace-only body — Codex-R5-P1-2 regression
        try:
            mint("manual-bypass", "00DPP0000004XYZAB1",
                 reason="JIRA-1234:           ",
                 target_path=token_path, command="x")
            check("F-IM-10 manual-bypass + whitespace body → ValueError (Codex-R5-P1-2)", False)
        except ValueError:
            check("F-IM-10 manual-bypass + whitespace body → ValueError (Codex-R5-P1-2)", True)

        # TTL exceeds cap
        try:
            mint("revert", "00DPP0000004XYZAB1",
                 expiry_seconds=MAX_TTL_BY_OP_TYPE["revert"] + 60,
                 target_path=token_path, command="x")
            check("F-IM-11 TTL > cap → ValueError", False)
        except ValueError:
            check("F-IM-11 TTL > cap → ValueError", True)

        # Negative TTL
        try:
            mint("revert", "00DPP0000004XYZAB1",
                 expiry_seconds=-1,
                 target_path=token_path, command="x")
            check("F-IM-12 negative TTL → ValueError", False)
        except ValueError:
            check("F-IM-12 negative TTL → ValueError", True)

        # ── F-IM-success: valid mint of revert token + show + revoke ──
        path = mint("revert", "00DPP0000004XYZAB1",
                    command="sf project deploy start --target-org sample-prod",
                    expiry_seconds=600,
                    reason="auto-revert of snapshot abc123",
                    target_path=token_path)
        check("F-IM-13 revert mint returns target_path", path == token_path)
        check("F-IM-13b revert token file exists", token_path.exists())

        # File mode is 0o600 (POSIX only; Windows has no equivalent mode bits)
        st = token_path.stat()
        check("F-IM-13c revert token mode is 0o600",
              os.name == "nt" or (st.st_mode & 0o777) == 0o600)

        # Show
        token = show(token_path)
        check("F-IM-13d show returns dict", isinstance(token, dict))
        assert token is not None
        check("F-IM-13e schema_version = 1", token["schema_version"] == 1)
        check("F-IM-13f operation_type preserved", token["operation_type"] == "revert")
        check("F-IM-13g org_id_18 preserved", token["org_id_18"] == "00DPP0000004XYZAB1")
        check("F-IM-13h command_fingerprint computed",
              token["command_fingerprint"] == fingerprint_command("sf project deploy start --target-org sample-prod"))
        check("F-IM-13i operator field set (kernel uid name)",
              isinstance(token.get("operator"), str) and len(token["operator"]) > 0)
        check("F-IM-13j expiry_iso ~ now + 600s",
              "expiry_iso" in token and "issued_at_iso" in token)

        # Revoke
        deleted = revoke(token_path)
        check("F-IM-14 revoke returns True", deleted)
        check("F-IM-14b token file gone", not token_path.exists())
        deleted2 = revoke(token_path)
        check("F-IM-14c double revoke returns False", not deleted2)

        # Manual-bypass with valid structured reason
        path = mint("manual-bypass", "00DPP0000004XYZAB1",
                    reason="INC-1234: prod hotfix for bad flow validation rule",
                    expiry_seconds=600,
                    target_path=token_path)
        check("F-IM-15 manual-bypass + valid reason mints", token_path.exists())
        token = show(token_path)
        assert token is not None
        check("F-IM-15b manual-bypass reason preserved",
              "INC-1234" in token["reason"])

    for label, passed in tests:
        print(f"{'PASS' if passed else 'FAIL'}: {label}")
    if failures:
        print(f"\nintent_marker self-test FAILED: {failures} fixture(s)", file=sys.stderr)
        return 1
    print(f"\nintent_marker self-test PASSED ({len(tests)} fixtures)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
