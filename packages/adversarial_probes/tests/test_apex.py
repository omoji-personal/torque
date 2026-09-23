"""Unit tests for Apex probe synthesis.

Apache-2.0.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jsc_probes.apex import generate_probes_for_class, parse_methods  # noqa: E402
from jsc_probes.cli import DEFAULT_APEX_API_VERSION, write_test_class  # noqa: E402


SAMPLE_CLASS = """
public with sharing class MyHandler {
    public static void process(List<Account> accs) {
        // ...
    }
    public Boolean isReady(Account a) {
        return a != null;
    }
    public void run() {
        // no params
    }
}
""".strip()


class TestParseMethods(unittest.TestCase):
    def test_parses_three_methods(self):
        methods = parse_methods(SAMPLE_CLASS)
        names = [m.name for m in methods]
        # Order may differ but all 3 should be present.
        for expected in ("process", "isReady", "run"):
            self.assertIn(expected, names)

    def test_parameters(self):
        methods = parse_methods(SAMPLE_CLASS)
        proc = next((m for m in methods if m.name == "process"), None)
        self.assertIsNotNone(proc)
        params = proc.parameters()
        self.assertEqual(len(params), 1)
        self.assertEqual(params[0][0], "List<Account>")

    def test_overloads_are_not_collapsed(self):
        # Audit 2026-05-30 P2: dedup keyed on method NAME silently dropped
        # overloads that differ only by parameter types — so only one arbitrary
        # overload got probed. Dedup now keys on the full signature.
        overload_class = """
public with sharing class Overloaded {
    public static void doIt(String a) {}
    public static void doIt(Integer a, Integer b) {}
    public static void doIt(List<Account> accs) {}
}
""".strip()
        methods = parse_methods(overload_class)
        do_it = [m for m in methods if m.name == "doIt"]
        self.assertEqual(len(do_it), 3, "all three doIt overloads must be kept")
        raws = {" ".join(m.params_raw.split()) for m in do_it}
        self.assertEqual(len(raws), 3, "each overload must have a distinct param signature")

    def test_true_duplicate_signature_collapses(self):
        # A genuine duplicate (identical name + params) must still collapse to one.
        dup_class = """
public class Dup {
    public static void same(String a) {}
    public static void same(String a) {}
}
""".strip()
        methods = parse_methods(dup_class)
        self.assertEqual(len([m for m in methods if m.name == "same"]), 1)


class TestGenerateProbes(unittest.TestCase):
    def test_includes_only_honest_drafts(self):
        out = generate_probes_for_class(SAMPLE_CLASS, "MyHandler")
        for expected in ("testNullInputs", "testEmptyCollections", "System.assert(false, 'DRAFT:"):
            self.assertIn(expected, out, f"expected {expected} in generated output")
        for removed in ("testBulk251", "testGovernorSaturation", "testFlsRunAs", "catch (Exception"):
            self.assertNotIn(removed, out)

    def test_class_naming(self):
        out = generate_probes_for_class(SAMPLE_CLASS, "MyHandler")
        self.assertIn("class MyHandlerAdversarialTest", out)

    def test_apex_syntax_landmarks(self):
        out = generate_probes_for_class(SAMPLE_CLASS, "MyHandler")
        self.assertIn("@isTest(SeeAllData=false)", out)
        self.assertIn("Test.startTest();", out)
        self.assertIn("Test.stopTest();", out)
        self.assertNotIn("System.runAs", out)


class TestMetaApiVersion(unittest.TestCase):
    def test_generated_meta_uses_named_constant(self):
        # Audit 2026-05-30 P2: apiVersion is a named constant, not an inline
        # literal; the generated meta.xml stamps that exact version.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "MyHandler.cls"
            src.write_text(SAMPLE_CLASS, encoding="utf-8")
            out = write_test_class(src, Path(td))
            meta = out.with_name(out.name + "-meta.xml")
            self.assertTrue(meta.exists(), "meta.xml must be written")
            self.assertIn(
                f"<apiVersion>{DEFAULT_APEX_API_VERSION}</apiVersion>",
                meta.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
