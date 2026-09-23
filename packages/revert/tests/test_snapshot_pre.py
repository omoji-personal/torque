#!/usr/bin/env python3
"""Tests for snapshot_pre.py — closes Codex-R5-P1-4 (substring collision in
fullName/type matching can falsely classify present components as absent).

Critical fixtures:
  - sf 2.107.6 actual JSON shape: missing component → state='Failed', no filePath
  - 'Account' (CustomObject fullName) inside 'AccountManager' problem text must
    NOT cause Account to be falsely classified absent (the substring-collision bug)
  - Mixed retrieve (1 present + 1 absent) classifies each correctly
  - Unknown failure shape → fail-closed (retrieve_failed, NOT absent)
  - Files reported present but missing on disk → retrieve_failed (fail-closed)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from revert.jsc_revert.snapshot_pre import (
    classify_retrieve_result, _extract_problem_tuple, _NOT_FOUND_PROBLEM_RE,
    _derive_metadata_path, _PATH_CONVENTIONS,
)


def main() -> int:  # noqa: C901
    failures = 0
    tests = []

    def check(label: str, condition: bool):
        nonlocal failures
        tests.append((label, condition))
        if not condition:
            failures += 1

    # ── F-SP-1..3: regex tuple extraction (Codex-R5-P1-4 core fix) ──
    extracted = _extract_problem_tuple(
        "Entity of type 'ApexClass' named 'JSC_DOES_NOT_EXIST' cannot be found"
    )
    check("F-SP-1 sf 2.107.6 'cannot be found' extracts (type, fullName)",
          extracted == ("ApexClass", "JSC_DOES_NOT_EXIST"))

    extracted = _extract_problem_tuple(
        "Entity of type 'CustomObject' named 'AccountManager' cannot be found"
    )
    check("F-SP-2 fullName extraction is exact (not substring)",
          extracted == ("CustomObject", "AccountManager"))
    # Critical: extracted fullName 'AccountManager' should NOT equal 'Account'
    check("F-SP-2b extracted fullName != 'Account' (substring collision check)",
          extracted[1] != "Account")

    # Unknown problem text → no extraction
    extracted = _extract_problem_tuple("Some other unrelated error message")
    check("F-SP-3 unknown problem text → None", extracted is None)

    # ── F-SP-4: classify sf 2.107.6 missing-component shape ──
    sf_2107_missing = {
        "result": {
            "status": "Succeeded",
            "files": [
                {
                    "type": "ApexClass",
                    "fullName": "JSC_DOES_NOT_EXIST_20260513_CODEX",
                    "state": "Failed",
                    "error": "",
                    # NO filePath — Codex empirically verified
                },
            ],
            "messages": [
                {
                    "fileName": "unpackaged/package.xml",  # NOT 'file'
                    "problem": "Entity of type 'ApexClass' named 'JSC_DOES_NOT_EXIST_20260513_CODEX' cannot be found",
                },
            ],
        },
    }
    with tempfile.TemporaryDirectory() as tmpd:
        result = classify_retrieve_result(sf_2107_missing, Path(tmpd))
    check("F-SP-4a sf 2.107.6 missing → 1 classification", len(result) == 1)
    check("F-SP-4b sf 2.107.6 missing → state='absent'",
          result[0].state == "absent")
    check("F-SP-4c sf 2.107.6 missing → preserves raw_problem",
          "cannot be found" in (result[0].raw_problem or ""))

    # ── F-SP-5 (CRITICAL): substring-collision regression ──
    # Codex-R5-P1-4: 'Account' file entry must NOT be classified absent
    # just because 'AccountManager' problem text contains the string 'Account'.
    sf_collision = {
        "result": {
            "status": "Succeeded",
            "files": [
                {
                    # Account exists — 'Created' state means sf retrieved it
                    "type": "CustomObject",
                    "fullName": "Account",
                    "state": "Created",
                    "filePath": "force-app/main/default/objects/Account/Account.object-meta.xml",
                },
                {
                    # AccountManager doesn't exist
                    "type": "CustomObject",
                    "fullName": "AccountManager",
                    "state": "Failed",
                },
            ],
            "messages": [
                {
                    "fileName": "unpackaged/package.xml",
                    # 'AccountManager' problem — must NOT match 'Account' file entry
                    "problem": "Entity of type 'CustomObject' named 'AccountManager' cannot be found",
                },
            ],
        },
    }
    with tempfile.TemporaryDirectory() as tmpd:
        # Pre-create the Account file so the present check works
        account_path = Path(tmpd) / "force-app" / "main" / "default" / "objects" / "Account" / "Account.object-meta.xml"
        account_path.parent.mkdir(parents=True, exist_ok=True)
        account_path.write_text("<?xml version='1.0'?><CustomObject></CustomObject>", encoding="utf-8")
        result = classify_retrieve_result(sf_collision, Path(tmpd))

    check("F-SP-5a substring-collision: 2 classifications", len(result) == 2)
    account_cls = next((c for c in result if c.fullName == "Account"), None)
    am_cls = next((c for c in result if c.fullName == "AccountManager"), None)
    check("F-SP-5b 'Account' → present (NOT falsely absent)",
          account_cls is not None and account_cls.state == "present")
    check("F-SP-5c 'AccountManager' → absent",
          am_cls is not None and am_cls.state == "absent")
    if account_cls and account_cls.state == "present":
        check("F-SP-5d 'Account' has checksum", account_cls.checksum is not None)

    # ── F-SP-6: Failed entry with NO matching not-found message → retrieve_failed ──
    # (fail-closed: don't assume absent; could be auth failure, network error, etc.)
    sf_unknown_fail = {
        "result": {
            "status": "Succeeded",
            "files": [
                {
                    "type": "ApexClass",
                    "fullName": "Foo",
                    "state": "Failed",
                    "error": "Connection timeout",
                },
            ],
            "messages": [
                {"fileName": "unpackaged/package.xml", "problem": "Network error"},
            ],
        },
    }
    with tempfile.TemporaryDirectory() as tmpd:
        result = classify_retrieve_result(sf_unknown_fail, Path(tmpd))
    check("F-SP-6a unknown failure shape → retrieve_failed (NOT absent — fail-closed)",
          result[0].state == "retrieve_failed")
    check("F-SP-6b retrieve_failed preserves raw_problem context",
          result[0].raw_problem and "Connection timeout" in result[0].raw_problem)

    # ── F-SP-7: Successfully reported file but missing on disk → retrieve_failed ──
    # (fail-closed: sf can lie or race conditions can occur)
    sf_lying = {
        "result": {
            "status": "Succeeded",
            "files": [
                {
                    "type": "ApexClass", "fullName": "Foo", "state": "Created",
                    "filePath": "classes/Foo.cls",  # but we won't pre-create it
                },
            ],
            "messages": [],
        },
    }
    with tempfile.TemporaryDirectory() as tmpd:
        result = classify_retrieve_result(sf_lying, Path(tmpd))
    check("F-SP-7 sf reports Created but file missing → retrieve_failed",
          result[0].state == "retrieve_failed")
    check("F-SP-7b raw_problem mentions disk mismatch",
          result[0].raw_problem and "missing on disk" in result[0].raw_problem)

    # ── F-SP-8: Mixed retrieve — 1 present + 1 absent + 1 retrieve_failed ──
    sf_mixed = {
        "result": {
            "status": "Succeeded",
            "files": [
                {"type": "ApexClass", "fullName": "Bar", "state": "Created",
                 "filePath": "force-app/main/default/classes/Bar.cls"},
                {"type": "ApexClass", "fullName": "Baz", "state": "Failed"},
                {"type": "Flow", "fullName": "Qux", "state": "Failed"},
            ],
            "messages": [
                {"fileName": "unpackaged/package.xml",
                 "problem": "Entity of type 'ApexClass' named 'Baz' cannot be found"},
                # Qux has no matching message → retrieve_failed
            ],
        },
    }
    with tempfile.TemporaryDirectory() as tmpd:
        bar_path = Path(tmpd) / "force-app" / "main" / "default" / "classes" / "Bar.cls"
        bar_path.parent.mkdir(parents=True, exist_ok=True)
        bar_path.write_text("// Bar", encoding="utf-8")
        result = classify_retrieve_result(sf_mixed, Path(tmpd))
    by_name = {c.fullName: c for c in result}
    check("F-SP-8a Bar (existing) → present", by_name["Bar"].state == "present")
    check("F-SP-8b Baz (matched not-found message) → absent", by_name["Baz"].state == "absent")
    check("F-SP-8c Qux (no matching message) → retrieve_failed", by_name["Qux"].state == "retrieve_failed")

    # ── F-SP-9: Missing 'result' key → ValueError (API shape change) ──
    try:
        classify_retrieve_result({"some_other_top_key": []}, Path("/tmp"))
        check("F-SP-9 missing 'result' → ValueError", False)
    except ValueError:
        check("F-SP-9 missing 'result' → ValueError", True)

    # ── F-SP-10: regex matches 'cannot be found' literal ──
    check("F-SP-10 regex matches Codex's exact reproduction string",
          _NOT_FOUND_PROBLEM_RE.search(
              "Entity of type 'ApexClass' named 'JSC_DOES_NOT_EXIST_20260513_CODEX' cannot be found"
          ) is not None)

    # ── F-SP-11: path conventions resolve for every registered type ──
    # Guards the 2026-07-28 extension. A type missing from _PATH_CONVENTIONS
    # derives no path, classifies retrieve_failed, and is silently NOT captured
    # — so this asserts each registered type actually resolves against a real
    # file laid out the way `sf project retrieve` writes one. CustomMetadata is
    # the reason the extension happened (JS business logic runs on CMDT), and
    # CustomObject is the shape that needed a new {name}-in-subdir branch.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "force-app" / "main" / "default"
        cases = [
            ("ApexClass",         "Foo",                    "classes/Foo.cls"),
            ("ApexTrigger",       "FooTrigger",             "triggers/FooTrigger.trigger"),
            ("Flow",              "My_Flow",                "flows/My_Flow.flow-meta.xml"),
            ("ValidationRule",    "Account.Rule_A",         "objects/Account/validationRules/Rule_A.validationRule-meta.xml"),
            ("CustomMetadata",    "JS_Conflict.Default",    "customMetadata/JS_Conflict.Default.md-meta.xml"),
            ("CustomField",       "Account.Foo__c",         "objects/Account/fields/Foo__c.field-meta.xml"),
            ("FieldSet",          "Other_Service__c.FS",    "objects/Other_Service__c/fieldSets/FS.fieldSet-meta.xml"),
            ("RecordType",        "Trust_Funds__c.Withdrawal", "objects/Trust_Funds__c/recordTypes/Withdrawal.recordType-meta.xml"),
            ("CustomObject",      "Foo__c",                 "objects/Foo__c/Foo__c.object-meta.xml"),
            ("PermissionSet",     "Example_Full_Access", "permissionsets/Example_Full_Access.permissionset-meta.xml"),
            ("FlexiPage",         "Rec_Page",               "flexipages/Rec_Page.flexipage-meta.xml"),
            ("Layout",            "ProductTransfer-Product Transfer Layout",
                                  "layouts/ProductTransfer-Product Transfer Layout.layout-meta.xml"),
            ("GlobalValueSet",    "Problem_Codes",          "globalValueSets/Problem_Codes.globalValueSet-meta.xml"),
            ("QuickAction",       "LogACall",               "quickActions/LogACall.quickAction-meta.xml"),
            ("CustomTab",         "Resource_Assignment__c", "tabs/Resource_Assignment__c.tab-meta.xml"),
            ("CustomApplication", "Demo__Example_Lightning",
                                  "applications/Demo__Example_Lightning.app-meta.xml"),
        ]
        for _t, _n, rel in cases:
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x", encoding="utf-8")
        unresolved = [f"{t}:{n}" for t, n, rel in cases
                      if _derive_metadata_path(t, n, Path(td)) != root / rel]
        check(f"F-SP-11 all {len(cases)} path conventions resolve"
              + (f" — UNRESOLVED: {', '.join(unresolved)}" if unresolved else ""),
              not unresolved)
        # Every registered type must appear above, or the extension can grow a
        # type whose convention nothing ever exercises.
        untested = sorted(set(_PATH_CONVENTIONS) - {t for t, _, _ in cases})
        check(f"F-SP-11b every _PATH_CONVENTIONS type is covered"
              + (f" — UNTESTED: {', '.join(untested)}" if untested else ""),
              not untested)

    for label, passed in tests:
        print(f"{'PASS' if passed else 'FAIL'}: {label}")
    if failures:
        print(f"\nsnapshot_pre self-test FAILED: {failures} fixture(s)", file=sys.stderr)
        return 1
    print(f"\nsnapshot_pre self-test PASSED ({len(tests)} fixtures)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
