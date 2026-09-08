#!/usr/bin/env python3
"""Tests for jsc_ai_prompt_regression package — OFFLINE only.

Validates contracts.py shape-validation logic + harness loading + CLI parsing.
The actual gemini dispatch is exercised via integration tests, not here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _run_cli(args: list[str], env_overrides: dict | None = None) -> tuple[int, str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]) + os.pathsep + os.environ.get("PYTHONPATH", "")
    if env_overrides:
        env.update(env_overrides)
    proc = subprocess.run(
        [sys.executable, "-m", "jsc_ai_prompt_regression.cli", *args],
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
    from jsc_ai_prompt_regression import contracts, harness

    # ── PARSE_TARGET_SPEC ────────────────────────────────────────────────
    spec = contracts.parse_target_spec_yaml({
        "target_name": "narrativeSummary",
        "configuration_set": "MyConfig",
        "mode": "Fields",
        "field_paths": ["narrativeSummary"],
    })
    check("F-PS-1 Fields spec parses", spec.target_name == "narrativeSummary")
    check("F-PS-1b mode is Fields", spec.mode == "Fields")
    check("F-PS-1c field_paths populated", spec.field_paths == ["narrativeSummary"])

    spec = contracts.parse_target_spec_yaml({
        "target_name": "rec",
        "configuration_set": "MyConfig",
        "mode": "Records",
        "sobject_type": "Demo__Benefit__c",
        "field_mappings": {"benefit_type": "Demo__Type__c"},
    })
    check("F-PS-2 Records spec parses", spec.mode == "Records")
    check("F-PS-2b sobject_type set", spec.sobject_type == "Demo__Benefit__c")

    # CMDT-style API name keys also accepted
    spec = contracts.parse_target_spec_yaml({
        "Target_Name__c": "via_cmdt_keys",
        "Configuration_Set__c": "X",
        "Mode__c": "Fields",
        "Field_Paths__c": "a, b\nc",
    })
    check("F-PS-3 CMDT API names accepted", spec.target_name == "via_cmdt_keys")
    check("F-PS-3b Field_Paths__c text parsed", spec.field_paths == ["a", "b", "c"])

    # text-form field_mappings (=> form)
    spec = contracts.parse_target_spec_yaml({
        "target_name": "x", "configuration_set": "y", "mode": "Records",
        "sobject_type": "Foo", "field_mappings": "k1=>SF1\nk2=>SF2",
    })
    check("F-PS-4 text-form field_mappings parsed",
          spec.field_mappings == {"k1": "SF1", "k2": "SF2"})

    # text-form field_mappings (JSON Field: ... | Salesforce Field: ... form)
    spec = contracts.parse_target_spec_yaml({
        "target_name": "x", "configuration_set": "y", "mode": "Records",
        "sobject_type": "Foo",
        "field_mappings": "JSON Field: foo | Salesforce Field: SF_Foo__c",
    })
    check("F-PS-4b friendly field_mappings parsed",
          spec.field_mappings == {"foo": "SF_Foo__c"})

    # Validation: bad mode rejected
    try:
        contracts.parse_target_spec_yaml({
            "target_name": "bad", "configuration_set": "x", "mode": "Garbage",
        })
        check("F-PS-5 bad mode rejected", False)
    except ValueError:
        check("F-PS-5 bad mode rejected", True)

    # Validation: invalid dotpath rejected
    try:
        contracts.parse_target_spec_yaml({
            "target_name": "bad", "configuration_set": "x", "mode": "Fields",
            "field_paths": ["valid", "not valid (with spaces)"],
        })
        check("F-PS-6 invalid dotpath rejected", False)
    except ValueError:
        check("F-PS-6 invalid dotpath rejected", True)

    # Records mode requires sobject_type
    try:
        contracts.parse_target_spec_yaml({
            "target_name": "x", "configuration_set": "y", "mode": "Records",
            "field_mappings": {"k": "v"},
        })
        check("F-PS-7 Records without sobject_type rejected", False)
    except ValueError:
        check("F-PS-7 Records without sobject_type rejected", True)

    # ── VALIDATE_AGAINST_SPEC ────────────────────────────────────────────
    field_spec = contracts.parse_target_spec_yaml({
        "target_name": "summary", "configuration_set": "x", "mode": "Fields",
        "base_path": "parentFieldUpdates",
        "field_paths": ["narrative_AI__c", "next_status_AI__c"],
    })
    record_spec = contracts.parse_target_spec_yaml({
        "target_name": "benefits", "configuration_set": "x", "mode": "Records",
        "base_path": "recordsToCreate", "sobject_type": "Demo__Benefit__c",
        "field_mappings": {"benefit_type": "Demo__Type__c", "amount": "Demo__Amount__c"},
    })

    # Happy path
    payload = {
        "parentFieldUpdates": {
            "narrative_AI__c": "ok",
            "next_status_AI__c": "Working",
        },
        "recordsToCreate": [
            {"benefit_type": "Housing", "amount": 1500.00},
            {"benefit_type": "Food", "amount": 480.00},
        ],
    }
    result = contracts.validate_against_spec(payload, [field_spec, record_spec])
    check("F-VA-1 happy path PASS", result.status == "PASS")
    check("F-VA-1b no findings", result.findings == [])

    # Missing base_path → P0
    payload_bad = {"parentFieldUpdates": {"narrative_AI__c": "ok", "next_status_AI__c": "ok"}}
    result = contracts.validate_against_spec(payload_bad, [field_spec, record_spec])
    check("F-VA-2 missing base_path FAILs", result.status == "FAIL")
    check("F-VA-2b P0 finding present",
          any(f.severity == "P0" for f in result.findings))

    # Fields-mode missing field_path → P1
    payload_partial = {
        "parentFieldUpdates": {"narrative_AI__c": "ok"},  # missing next_status_AI__c
        "recordsToCreate": [],
    }
    result = contracts.validate_against_spec(payload_partial, [field_spec, record_spec])
    check("F-VA-3 missing field_path FAILs", result.status == "FAIL")
    p1_findings = [f for f in result.findings if f.severity == "P1"]
    check("F-VA-3b P1 finding for missing field_path",
          any("next_status_AI__c" in f.path for f in p1_findings))

    # Fields-mode null value → P1 by default (R1 codex-P1-05 fix)
    payload_null = {
        "parentFieldUpdates": {"narrative_AI__c": None, "next_status_AI__c": "Open"},
        "recordsToCreate": [],
    }
    result = contracts.validate_against_spec(payload_null, [field_spec, record_spec])
    check("F-VA-4 null value → P1 (default strict)", result.status == "FAIL")
    check("F-VA-4b P1 finding present",
          any(f.severity == "P1" for f in result.findings))

    # allow_null_required=True downgrades null to P2 (operator opt-out)
    field_spec_lenient = contracts.parse_target_spec_yaml({
        "target_name": "summary", "configuration_set": "x", "mode": "Fields",
        "base_path": "parentFieldUpdates",
        "field_paths": ["narrative_AI__c"],
        "allow_null_required": True,
    })
    result = contracts.validate_against_spec(
        {"parentFieldUpdates": {"narrative_AI__c": None}}, [field_spec_lenient]
    )
    check("F-VA-4c allow_null_required downgrades to P2",
          result.status == "PASS" and any(f.severity == "P2" for f in result.findings))

    # Fields-mode non-scalar value → P1 (R1 codex-P1-05)
    payload_nonscalar = {
        "parentFieldUpdates": {"narrative_AI__c": {"nested": "object"}, "next_status_AI__c": "Open"},
        "recordsToCreate": [],
    }
    result = contracts.validate_against_spec(payload_nonscalar, [field_spec, record_spec])
    check("F-VA-4d non-scalar value → P1",
          result.status == "FAIL" and
          any("scalar" in f.message for f in result.findings))

    # Records-mode item missing mapped key → P1
    payload_recmiss = {
        "parentFieldUpdates": {"narrative_AI__c": "ok", "next_status_AI__c": "ok"},
        "recordsToCreate": [
            {"benefit_type": "Housing", "amount": 1500.00},
            {"benefit_type": "Food"},  # missing amount
        ],
    }
    result = contracts.validate_against_spec(payload_recmiss, [field_spec, record_spec])
    check("F-VA-5 records missing key FAILs", result.status == "FAIL")
    check("F-VA-5b P1 cites the missing index",
          any("[1]" in f.path and "amount" in f.path for f in result.findings))

    # Top-level non-dict → P0
    result = contracts.validate_against_spec("a string", [field_spec])
    check("F-VA-6 non-dict payload P0", result.status == "FAIL")
    check("F-VA-6b finding mentions root",
          any("<root>" in f.target_name for f in result.findings))

    # Inactive spec is skipped
    inactive = contracts.parse_target_spec_yaml({
        "target_name": "x", "configuration_set": "y", "mode": "Fields",
        "field_paths": ["definitelyNotPresent"], "active": False,
    })
    result = contracts.validate_against_spec({"foo": "bar"}, [inactive])
    check("F-VA-7 inactive spec skipped (PASS)", result.status == "PASS")

    # R1 codex-P2-05: string boolean coercion — 'false' must be False, not bool('false')==True
    inactive_str = contracts.parse_target_spec_yaml({
        "target_name": "x", "configuration_set": "y", "mode": "Fields",
        "field_paths": ["nope"], "Active__c": "false",
    })
    check("F-VA-8 string 'false' coerced to False", inactive_str.active is False)

    inactive_str_t = contracts.parse_target_spec_yaml({
        "target_name": "x", "configuration_set": "y", "mode": "Fields",
        "field_paths": ["x"], "Active__c": "true",
    })
    check("F-VA-8b string 'true' coerced to True", inactive_str_t.active is True)

    # Ambiguous boolean string rejected
    try:
        contracts.parse_target_spec_yaml({
            "target_name": "x", "configuration_set": "y", "mode": "Fields",
            "field_paths": ["x"], "Active__c": "garbage_string",
        })
        check("F-VA-8c ambiguous bool string raises", False)
    except ValueError:
        check("F-VA-8c ambiguous bool string raises", True)

    # R1 CONSENSUS-5: strict_keys flags unknown extras as P1
    strict_spec = contracts.parse_target_spec_yaml({
        "target_name": "x", "configuration_set": "y", "mode": "Fields",
        "base_path": "p",
        "field_paths": ["a"], "strict_keys": True,
    })
    result = contracts.validate_against_spec(
        {"p": {"a": "ok", "extra_key": "leak"}}, [strict_spec]
    )
    check("F-VA-9 strict_keys flags unknown key as P1",
          result.status == "FAIL" and
          any(f.severity == "P1" and "extra_key" in f.path for f in result.findings))

    # Without strict_keys, extras are silently allowed (default behavior)
    permissive_spec = contracts.parse_target_spec_yaml({
        "target_name": "x", "configuration_set": "y", "mode": "Fields",
        "base_path": "p", "field_paths": ["a"],
    })
    result = contracts.validate_against_spec(
        {"p": {"a": "ok", "extra_key": "leak"}}, [permissive_spec]
    )
    check("F-VA-9b non-strict allows extras", result.status == "PASS")

    # R1 CONSENSUS-5(a): brace-depth state machine — banner + footer with brace
    payload, err = contracts.safe_parse_json('{"a": 1}\nFootnote: see {section 4} for details.')
    check("F-SP-5 brace-depth parser handles trailing brace prose",
          payload == {"a": 1})

    # JSON inside string literal must NOT be miscounted
    payload, err = contracts.safe_parse_json('{"a": "value with } inside string"}')
    check("F-SP-6 string-literal braces not misread",
          payload == {"a": "value with } inside string"})

    # Code-fence stripping
    payload, err = contracts.safe_parse_json('```json\n{"a": "ok"}\n```\nThanks!')
    check("F-SP-7 code-fence stripped",
          payload == {"a": "ok"})

    # No JSON at all
    payload, err = contracts.safe_parse_json("Just prose, no braces here")
    check("F-SP-8 no-brace fails cleanly",
          payload is None and "balanced" in err)

    # ── SAFE_PARSE_JSON ──────────────────────────────────────────────────
    payload, err = contracts.safe_parse_json("")
    check("F-SP-1 empty → None+detail", payload is None and err)

    payload, err = contracts.safe_parse_json("not json at all")
    check("F-SP-2 non-JSON → None+detail", payload is None and err)

    payload, err = contracts.safe_parse_json('{"a": 1}')
    check("F-SP-3 valid JSON parses", payload == {"a": 1})

    payload, err = contracts.safe_parse_json('Banner line\n{"a": 2}\nTrailing noise')
    check("F-SP-4 banner-prefixed JSON parses", payload == {"a": 2})

    # ── HARNESS LOAD_FIXTURE ─────────────────────────────────────────────
    # The shipped fixture should load cleanly
    shipped = (Path(__file__).resolve().parents[1]
               / "jsc_ai_prompt_regression" / "fixtures" / "insight_action_minimal")
    check("F-LF-1 shipped fixture dir exists", shipped.exists())
    if shipped.exists():
        fixture = harness.load_fixture(shipped)
        check("F-LF-1b shipped fixture loads", fixture.name == "insight_action_minimal")
        check("F-LF-1c shipped fixture has 2 specs", len(fixture.specs) == 2)
        check("F-LF-1d prompt + input loaded",
              "{{INPUT}}" in fixture.prompt_template and len(fixture.input_text) > 100)

    # Invalid fixture (missing files) → ValueError
    with tempfile.TemporaryDirectory() as tmp:
        try:
            harness.load_fixture(tmp)
            check("F-LF-2 fixture missing files raises", False)
        except ValueError:
            check("F-LF-2 fixture missing files raises", True)

    # ── HARNESS REPLAY (no gemini, no live network) ──────────────────────
    # When gemini is absent OR shipped fixture exists but we don't actually
    # invoke a model, we'd just check that the path leading up to invocation
    # doesn't crash. Use the shipped fixture for state-only validation.
    if shipped.exists():
        fixture = harness.load_fixture(shipped)
        # _build_prompt deterministic
        built = harness._build_prompt(fixture.prompt_template, "TEST INPUT")
        check("F-HR-1 _build_prompt substitutes placeholder",
              "TEST INPUT" in built and "{{INPUT}}" not in built)

        # template without placeholder appends input
        no_placeholder = "Just a prompt body."
        appended = harness._build_prompt(no_placeholder, "DATA")
        check("F-HR-1b _build_prompt appends when no placeholder",
              appended.endswith("DATA"))

    # gemini_available is deterministic bool
    check("F-HR-2 gemini_available returns bool",
          isinstance(harness.gemini_available(), bool))

    # replay_directory on missing root returns []
    out = harness.replay_directory("/tmp/no-such-fixtures-root-jsc-ai-test")
    check("F-HR-3 missing fixtures root → [] (no crash)", out == [])

    # ── CLI ──────────────────────────────────────────────────────────────
    code, out, err = _run_cli(["--help"])
    check("F-CLI-1 --help exits 0", code == 0)
    check("F-CLI-1b --help mentions replay + check-env",
          "replay" in out and "check-env" in out)

    code, out, err = _run_cli(["check-env"])
    payload = json.loads(out) if out.strip().startswith("{") else {}
    check("F-CLI-2 check-env emits JSON", isinstance(payload, dict) and "gemini_available" in payload)

    # replay against a missing fixture exits 2
    code, out, err = _run_cli(["replay", "/tmp/no-such-fixture-jsc-ai-test"])
    check("F-CLI-3 missing fixture exits 2", code == 2)

    # replay-all against a missing root exits 2
    code, out, err = _run_cli(["replay-all", "/tmp/no-such-fixtures-root-jsc-ai-test"])
    check("F-CLI-4 missing fixtures root exits 2", code == 2)

    for label, passed in tests:
        print(f"{'PASS' if passed else 'FAIL'}: {label}")
    if failures:
        print(f"\nai_prompt_regression self-test FAILED: {failures} fixture(s)", file=sys.stderr)
        return 1
    print(f"\nai_prompt_regression self-test PASSED ({len(tests)} fixtures)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
