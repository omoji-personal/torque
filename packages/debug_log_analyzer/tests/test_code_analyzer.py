"""Unit tests for the Salesforce Code Analyzer v5 wrapper (Task B2(a)).

Parses a CAPTURED, hand-authored v5 `sf code-analyzer run` JSON fixture (a mix
of severities 1-5) and asserts:
  - each violation maps to the right JSC P0/P1/P2 band,
  - the report renders,
  - the exit-code logic is non-zero when a P0/P1 is present and zero when only
    P2s remain.

Does NOT call sf — all assertions go through the offline --json-input path /
direct function calls. Mirrors the existing test_parsers.py unittest style.

Adopted 2026-06-13 (Phase B2(a)).
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jsc_loganalyzer import code_analyzer  # noqa: E402
from jsc_loganalyzer.score import score_findings  # noqa: E402


# A realistic v5 `sf code-analyzer run --view detail` JSON report, hand-authored
# from the real shape captured 2026-06-13 (sf code-analyzer 5.x / engine bundle
# 0.47.0). Mix of severities: one sev1, one sev2, one sev3, one sev4, one sev5.
FIXTURE = {
    "runDir": "/tmp/jsc-fixture/",
    "violationCounts": {"total": 5, "sev1": 1, "sev2": 1, "sev3": 1, "sev4": 1, "sev5": 1},
    "versions": {"code-analyzer": "0.47.0", "pmd": "0.40.0"},
    "violations": [
        {
            "rule": "ApexBadCrypto",
            "engine": "pmd",
            "severity": 1,
            "tags": ["Recommended", "Security", "Apex"],
            "primaryLocationIndex": 0,
            "locations": [
                {"file": "/tmp/jsc-fixture/Crypto.cls", "startLine": 12,
                 "startColumn": 5, "endLine": 12, "endColumn": 40}
            ],
            "message": "Hardcoded IV/key used with Crypto",
            "resources": ["https://example.invalid/badcrypto"],
        },
        {
            "rule": "ApexCRUDViolation",
            "engine": "pmd",
            "severity": 2,
            "tags": ["Recommended", "Security", "Apex"],
            "primaryLocationIndex": 0,
            "locations": [
                {"file": "/tmp/jsc-fixture/Sample.cls", "startLine": 4,
                 "startColumn": 31, "endLine": 4, "endColumn": 55}
            ],
            "message": "Validate CRUD permission before SOQL/DML operation or enforce user mode",
            "resources": ["https://example.invalid/crud"],
        },
        {
            "rule": "OperationWithLimitsInLoop",
            "engine": "pmd",
            "severity": 3,
            "tags": ["Recommended", "Performance", "Apex"],
            "primaryLocationIndex": 0,
            "locations": [
                {"file": "/tmp/jsc-fixture/Sample.cls", "startLine": 4,
                 "startColumn": 31, "endLine": 4, "endColumn": 55}
            ],
            "message": "Avoid operations in loops that may hit governor limits",
            "resources": ["https://example.invalid/loop"],
        },
        {
            "rule": "ApexDoc",
            "engine": "pmd",
            "severity": 4,
            "tags": ["Recommended", "Documentation", "Apex"],
            "primaryLocationIndex": 0,
            "locations": [
                {"file": "/tmp/jsc-fixture/Sample.cls", "startLine": 1,
                 "startColumn": 8, "endLine": 7, "endColumn": 2}
            ],
            "message": "Missing ApexDoc comment",
            "resources": ["https://example.invalid/apexdoc"],
        },
        {
            "rule": "VfCsrf",
            "engine": "pmd",
            "severity": 5,
            "tags": ["Recommended", "Security"],
            "primaryLocationIndex": 0,
            "locations": [
                {"file": "/tmp/jsc-fixture/Page.page", "startLine": 1,
                 "startColumn": 1, "endLine": 1, "endColumn": 10}
            ],
            "message": "Informational note",
            "resources": ["https://example.invalid/csrf"],
        },
    ],
}

# A P2-only fixture (only sev3-5 violations) — exercises the zero-exit path.
FIXTURE_P2_ONLY = {
    "runDir": "/tmp/jsc-fixture/",
    "violationCounts": {"total": 2, "sev1": 0, "sev2": 0, "sev3": 1, "sev4": 1, "sev5": 0},
    "versions": {"code-analyzer": "0.47.0"},
    "violations": [
        {
            "rule": "UnusedLocalVariable",
            "engine": "pmd",
            "severity": 3,
            "primaryLocationIndex": 0,
            "locations": [{"file": "/tmp/jsc-fixture/Sample.cls", "startLine": 4}],
            "message": "Variable 'a' defined but not used",
        },
        {
            "rule": "ApexDoc",
            "engine": "pmd",
            "severity": 4,
            "primaryLocationIndex": 0,
            "locations": [{"file": "/tmp/jsc-fixture/Sample.cls", "startLine": 1}],
            "message": "Missing ApexDoc comment",
        },
    ],
}

# A clean report — no violations.
FIXTURE_CLEAN = {
    "runDir": "/tmp/jsc-fixture/",
    "violationCounts": {"total": 0, "sev1": 0, "sev2": 0, "sev3": 0, "sev4": 0, "sev5": 0},
    "versions": {"code-analyzer": "0.47.0"},
    "violations": [],
}


class TestSeverityMapping(unittest.TestCase):
    def test_sev1_maps_p0(self):
        self.assertEqual(code_analyzer.map_severity(1), 1)

    def test_sev2_maps_p1(self):
        self.assertEqual(code_analyzer.map_severity(2), 2)

    def test_sev3_4_5_map_p2(self):
        self.assertEqual(code_analyzer.map_severity(3), 3)
        self.assertEqual(code_analyzer.map_severity(4), 3)
        self.assertEqual(code_analyzer.map_severity(5), 3)

    def test_unknown_severity_falls_back_to_p2(self):
        self.assertEqual(code_analyzer.map_severity(99), 3)
        self.assertEqual(code_analyzer.map_severity(0), 3)


class TestViolationsToFindings(unittest.TestCase):
    def test_mixed_fixture_maps_to_expected_bands(self):
        findings = code_analyzer.violations_to_findings(FIXTURE)
        self.assertEqual(len(findings), 5)
        sevs = [f.severity for f in findings]
        # sev1->P0, sev2->P1, sev3/4/5->P2 (three P2s).
        self.assertEqual(sevs.count(1), 1, f"expected 1 P0: {sevs}")
        self.assertEqual(sevs.count(2), 1, f"expected 1 P1: {sevs}")
        self.assertEqual(sevs.count(3), 3, f"expected 3 P2: {sevs}")

    def test_finding_carries_engine_rule_and_location(self):
        findings = code_analyzer.violations_to_findings(FIXTURE)
        p0 = next(f for f in findings if f.severity == 1)
        self.assertEqual(p0.category, "pmd:ApexBadCrypto")
        self.assertEqual(p0.line, 12)
        self.assertEqual(p0.context, "/tmp/jsc-fixture/Crypto.cls:12")
        self.assertIn("Crypto", p0.message)

    def test_missing_locations_does_not_crash(self):
        report = {"violations": [
            {"rule": "X", "engine": "pmd", "severity": 2, "message": "no loc"}
        ]}
        findings = code_analyzer.violations_to_findings(report)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, 2)
        self.assertEqual(findings[0].line, 0)
        self.assertEqual(findings[0].context, "")

    def test_primary_location_index_honored(self):
        report = {"violations": [{
            "rule": "X", "engine": "pmd", "severity": 3, "message": "m",
            "primaryLocationIndex": 1,
            "locations": [
                {"file": "/a.cls", "startLine": 1},
                {"file": "/b.cls", "startLine": 99},
            ],
        }]}
        findings = code_analyzer.violations_to_findings(report)
        self.assertEqual(findings[0].line, 99)
        self.assertEqual(findings[0].context, "/b.cls:99")


class TestExitCodeLogic(unittest.TestCase):
    def test_has_blocking_true_when_p0_present(self):
        findings = code_analyzer.violations_to_findings(FIXTURE)
        self.assertTrue(code_analyzer.has_blocking(findings))

    def test_has_blocking_false_when_only_p2(self):
        findings = code_analyzer.violations_to_findings(FIXTURE_P2_ONLY)
        self.assertFalse(code_analyzer.has_blocking(findings))

    def test_has_blocking_false_when_clean(self):
        findings = code_analyzer.violations_to_findings(FIXTURE_CLEAN)
        self.assertFalse(code_analyzer.has_blocking(findings))


class TestRender(unittest.TestCase):
    def test_report_renders_with_score_and_findings(self):
        findings = code_analyzer.violations_to_findings(FIXTURE)
        text = code_analyzer.render_report(findings, FIXTURE)
        self.assertIn("health score", text)
        self.assertIn("P0", text)
        self.assertIn("ApexBadCrypto", text)
        # Score reflects deductions: 100 - 15(P0) - 5(P1) - 3*1(P2) = 77.
        self.assertIn("77/100", text)

    def test_clean_report_renders(self):
        findings = code_analyzer.violations_to_findings(FIXTURE_CLEAN)
        text = code_analyzer.render_report(findings, FIXTURE_CLEAN)
        self.assertIn("100/100", text)
        self.assertIn("Clean", text)


class TestMainOfflinePath(unittest.TestCase):
    """End-to-end via main(--json-input ...) — proves CLI wiring without sf."""

    def _write_fixture(self, data: dict) -> str:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", prefix="jsc-ca-fix-", suffix=".json", delete=False
        )
        json.dump(data, tmp)
        tmp.close()
        return tmp.name

    def test_main_nonzero_exit_when_blocking(self):
        path = self._write_fixture(FIXTURE)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = code_analyzer.main(["--json-input", path])
        Path(path).unlink()
        self.assertEqual(rc, 1, "expected non-zero exit when P0/P1 present")
        self.assertIn("health score", out.getvalue())

    def test_main_zero_exit_when_only_p2(self):
        path = self._write_fixture(FIXTURE_P2_ONLY)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = code_analyzer.main(["--json-input", path])
        Path(path).unlink()
        self.assertEqual(rc, 0, "expected zero exit when only P2s present")

    def test_main_zero_exit_when_clean(self):
        path = self._write_fixture(FIXTURE_CLEAN)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = code_analyzer.main(["--json-input", path])
        Path(path).unlink()
        self.assertEqual(rc, 0)

    def test_main_json_output_shape(self):
        path = self._write_fixture(FIXTURE)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = code_analyzer.main(["--json-input", path, "--json"])
        Path(path).unlink()
        self.assertEqual(rc, 1)
        result = json.loads(out.getvalue())
        self.assertIn("score", result)
        self.assertIn("findings", result)
        self.assertEqual(result["summary"]["p0_count"], 1)
        self.assertEqual(result["summary"]["p1_count"], 1)
        self.assertEqual(result["summary"]["p2_count"], 3)

    def test_main_bad_json_input_returns_2(self):
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = code_analyzer.main(["--json-input", "/nonexistent/path.json"])
        self.assertEqual(rc, 2)


class TestScoreConsistency(unittest.TestCase):
    def test_score_matches_score_findings(self):
        findings = code_analyzer.violations_to_findings(FIXTURE)
        # 100 - 15 - 5 - 3 = 77
        self.assertEqual(score_findings(findings), 77)


if __name__ == "__main__":
    unittest.main()
