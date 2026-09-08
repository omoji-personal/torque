"""Unit tests for debug-log parsers.

Adopted 2026-05-04 from claudeblazer (Apache-2.0). TAA Phase 5 P2-3.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jsc_loganalyzer.parsers import (  # noqa: E402
    parse_fatal_swallowed,
    parse_governor_asymptotic,
    parse_missing_callout_response,
    parse_trigger_recursion,
    parse_unbounded_soql,
    run_all_parsers,
)
from jsc_loganalyzer.score import score_findings  # noqa: E402


def _lines(text: str) -> list[str]:
    return text.splitlines()


class TestParseFatalSwallowed(unittest.TestCase):
    def test_swallowed_fatal_detected(self):
        log = """09:00:00.0 |FATAL_ERROR|caught\n09:00:00.1 |STATEMENT_EXECUTE|continuing\n09:00:00.2 |HEAP_ALLOCATE|"""
        findings = parse_fatal_swallowed(log, _lines(log))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, 1)

    def test_surfaced_fatal_not_flagged(self):
        log = """09:00:00.0 |FATAL_ERROR|exception\n09:00:00.1 |EXECUTION_FINISHED|"""
        findings = parse_fatal_swallowed(log, _lines(log))
        self.assertEqual(len(findings), 0)


class TestParseGovernorAsymptotic(unittest.TestCase):
    def test_high_consumption_p0(self):
        log = "Maximum CPU time: 9500 out of 10000"
        findings = parse_governor_asymptotic(log, _lines(log))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, 1)

    def test_moderate_consumption_p1(self):
        log = "Number of SOQL queries: 80 out of 100"
        findings = parse_governor_asymptotic(log, _lines(log))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, 2)

    def test_low_consumption_no_finding(self):
        log = "Maximum heap size: 1000000 out of 6000000"
        findings = parse_governor_asymptotic(log, _lines(log))
        self.assertEqual(len(findings), 0)


class TestParseMissingCalloutResponse(unittest.TestCase):
    def test_missing_response_detected(self):
        log = "|CALLOUT_REQUEST|GET https://example.com/api"
        findings = parse_missing_callout_response(log, _lines(log))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, 1)

    def test_paired_request_response_clean(self):
        log = "|CALLOUT_REQUEST|GET https://example.com/api\n|CALLOUT_RESPONSE|200 OK"
        findings = parse_missing_callout_response(log, _lines(log))
        self.assertEqual(len(findings), 0)


class TestParseTriggerRecursion(unittest.TestCase):
    def test_deep_recursion_detected(self):
        log = "\n".join([
            "|CODE_UNIT_STARTED|[EXTERNAL]|Trigger:AccountTrigger" for _ in range(8)
        ])
        findings = parse_trigger_recursion(log, _lines(log))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, 1)

    def test_normal_invocation_clean(self):
        log = "|CODE_UNIT_STARTED|[EXTERNAL]|Trigger:AccountTrigger\n|CODE_UNIT_FINISHED|"
        findings = parse_trigger_recursion(log, _lines(log))
        self.assertEqual(len(findings), 0)


class TestParseUnboundedSoql(unittest.TestCase):
    def test_no_where_no_limit_flagged(self):
        log = "|SOQL_EXECUTE_BEGIN|Aggregations:0|SELECT Id FROM Account"
        findings = parse_unbounded_soql(log, _lines(log))
        self.assertEqual(len(findings), 1)

    def test_with_where_clean(self):
        log = "|SOQL_EXECUTE_BEGIN|Aggregations:0|SELECT Id FROM Account WHERE Name = 'X'"
        findings = parse_unbounded_soql(log, _lines(log))
        self.assertEqual(len(findings), 0)

    def test_with_limit_clean(self):
        log = "|SOQL_EXECUTE_BEGIN|Aggregations:0|SELECT Id FROM Account LIMIT 10"
        findings = parse_unbounded_soql(log, _lines(log))
        self.assertEqual(len(findings), 0)


class TestParseUnboundedSoqlMultiLine(unittest.TestCase):
    """Robust, fixture-driven multi-line SOQL detector (TAA B3).

    Apex debug logs wrap long SOQL statements across multiple physical lines.
    Each log EVENT begins with a `HH:MM:SS.s (nanos)|EVENT|` marker; wrapped
    SQL continuation fragments do NOT. The detector must reconstruct the full
    statement (stopping at the next event marker) before the WHERE/LIMIT check.
    """

    def test_multiline_with_where_not_flagged(self):
        # Real apex-log-wrapped lines: BEGIN line carries SELECT, the FROM and
        # WHERE land on continuation fragments, then a SOQL_EXECUTE_END marker.
        log = "\n".join([
            "09:00:00.0 (100)|SOQL_EXECUTE_BEGIN|[42]|Aggregations:0|SELECT Id, Name,",
            "    Phone, Email",
            "FROM Account",
            "WHERE Name = 'Acme'",
            "09:00:00.5 (500)|SOQL_EXECUTE_END|[42]|Rows:1",
        ])
        findings = parse_unbounded_soql(log, _lines(log))
        self.assertEqual(len(findings), 0, f"multi-line bounded query flagged: {findings}")

    def test_single_line_truly_unbounded_flagged(self):
        log = "09:00:00.0 (100)|SOQL_EXECUTE_BEGIN|[42]|Aggregations:0|SELECT Id FROM Account"
        findings = parse_unbounded_soql(log, _lines(log))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, 2)

    def test_limit_on_continuation_line_not_flagged(self):
        # The only LIMIT is on a wrapped continuation fragment.
        log = "\n".join([
            "09:00:00.0 (100)|SOQL_EXECUTE_BEGIN|[7]|Aggregations:0|SELECT Id",
            "FROM Contact",
            "LIMIT 50",
            "09:00:00.2 (200)|SOQL_EXECUTE_END|[7]|Rows:50",
        ])
        findings = parse_unbounded_soql(log, _lines(log))
        self.assertEqual(len(findings), 0, f"continuation LIMIT not honored: {findings}")

    def test_reconstruction_stops_at_next_event_marker(self):
        # A truly-unbounded SELECT immediately followed by an UNRELATED event
        # marker — reconstruction must STOP at the marker (not swallow the next
        # event into the statement), and the SELECT is still flagged unbounded.
        log = "\n".join([
            "09:00:00.0 (100)|SOQL_EXECUTE_BEGIN|[3]|Aggregations:0|SELECT Id FROM Account",
            "09:00:00.1 (150)|HEAP_ALLOCATE|[3]|Bytes:8",
            "09:00:00.2 (200)|SOQL_EXECUTE_BEGIN|[9]|Aggregations:0|SELECT Id FROM Contact WHERE Id != null",
            "09:00:00.3 (300)|SOQL_EXECUTE_END|[9]|Rows:0",
        ])
        findings = parse_unbounded_soql(log, _lines(log))
        # First query unbounded → flagged; second has WHERE → not flagged.
        self.assertEqual(len(findings), 1, f"expected exactly the first query flagged: {findings}")
        self.assertIn("SELECT Id FROM Account", findings[0].context)
        # Must NOT have swallowed the HEAP_ALLOCATE marker or the next SELECT.
        self.assertNotIn("HEAP_ALLOCATE", findings[0].context)
        self.assertNotIn("Contact", findings[0].context)

    def test_limit_value_field_not_a_false_positive(self):
        # A field literally named Limit_Value__c must NOT satisfy the LIMIT check
        # (the word-boundary check + space-padded heritage refutes this).
        log = "09:00:00.0 (100)|SOQL_EXECUTE_BEGIN|[1]|Aggregations:0|SELECT Id, Limit_Value__c FROM Account"
        findings = parse_unbounded_soql(log, _lines(log))
        self.assertEqual(len(findings), 1, "Limit_Value__c should NOT count as a LIMIT clause")


class TestCliSinceAggregation(unittest.TestCase):
    """--since <ts> fetches + aggregates N logs (fetch subprocess mocked)."""

    def test_since_fetches_and_aggregates_multiple_logs(self):
        from unittest import mock

        from jsc_loganalyzer import cli

        # Two logs, each carrying one distinct unbounded-SOQL finding.
        log_a = "09:00:00.0 (1)|SOQL_EXECUTE_BEGIN|[1]|Aggregations:0|SELECT Id FROM Account"
        log_b = "09:00:00.0 (1)|SOQL_EXECUTE_BEGIN|[1]|Aggregations:0|SELECT Id FROM Contact"

        captured = {}

        def fake_fetch_recent(target_org, since_iso, limit):
            captured["target_org"] = target_org
            captured["since_iso"] = since_iso
            captured["limit"] = limit
            return [log_a, log_b]

        out = io.StringIO()
        with mock.patch.object(cli, "fetch_recent_logs", side_effect=fake_fetch_recent), \
                contextlib.redirect_stdout(out):
            rc = cli.main([
                "--target-org", "sf-test",
                "--since", "2026-06-13T10:00:00Z",
                "--json",
            ])
        self.assertEqual(rc, 0)
        self.assertEqual(captured["target_org"], "sf-test")
        self.assertEqual(captured["since_iso"], "2026-06-13T10:00:00Z")
        result = json.loads(out.getvalue())
        # Both logs analyzed → aggregated findings (one unbounded SOQL each).
        unbounded = [f for f in result["findings"] if f["category"] == "unbounded-soql"]
        self.assertEqual(len(unbounded), 2, f"expected aggregation across 2 logs: {result}")

    def test_since_deploy_maps_to_bounded_default(self):
        from unittest import mock

        from jsc_loganalyzer import cli

        captured = {}

        def fake_fetch_recent(target_org, since_iso, limit):
            captured["since_iso"] = since_iso
            return ["09:00:00.0 (1)|SOQL_EXECUTE_BEGIN|[1]|Aggregations:0|SELECT Id FROM Account"]

        out = io.StringIO()
        with mock.patch.object(cli, "fetch_recent_logs", side_effect=fake_fetch_recent), \
                contextlib.redirect_stdout(out):
            rc = cli.main(["--target-org", "sf-test", "--since-deploy", "--json"])
        self.assertEqual(rc, 0)
        # --since-deploy resolves to a real bounded window (now - 600s), NOT None
        # and NOT the single-most-recent path.
        self.assertIsNotNone(captured.get("since_iso"))
        self.assertRegex(
            captured["since_iso"],
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
        )

    def test_limit_hard_capped(self):
        from jsc_loganalyzer import cli

        # Even if a caller asks for more than the cap, the query LIMIT is capped.
        self.assertEqual(cli.MAX_LOOKBACK_LOGS, 10)


class TestScore(unittest.TestCase):
    def test_clean_log_scores_100(self):
        findings = run_all_parsers("Some clean log content with no issues")
        score = score_findings(findings)
        self.assertEqual(score, 100)

    def test_dirty_log_scores_low(self):
        log = "\n".join([
            "|FATAL_ERROR|caught\n|STATEMENT_EXECUTE|continuing\n|HEAP_ALLOCATE|",
            "Maximum CPU time: 9500 out of 10000",
            "|CALLOUT_REQUEST|GET https://example.com/",
        ])
        findings = run_all_parsers(log)
        score = score_findings(findings)
        # Several P0 findings should drag score below 70.
        self.assertLess(score, 70, f"expected <70, got {score}; findings={findings}")


if __name__ == "__main__":
    unittest.main()
