from __future__ import annotations
import sys
from pathlib import Path
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jsc_browser_tests.assertions.soql import soql_assert
from jsc_browser_tests.sf_client import FakeSfClient

def test_soql_assert_pass_on_matching_field():
    fake = FakeSfClient(query_results={
        "SELECT FirstName FROM Contact WHERE Id='003x'": [{"FirstName": "Jane"}],
    })
    sr = soql_assert(fake, "row exists",
                     "SELECT FirstName FROM Contact WHERE Id='003x'",
                     expect={"FirstName": "Jane"})
    assert sr.status == "PASS"
    assert sr.side_effects["rows"][0]["FirstName"] == "Jane"

def test_soql_assert_fail_on_wrong_value():
    fake = FakeSfClient(query_results={
        "SELECT FirstName FROM Contact WHERE Id='003x'": [{"FirstName": "WRONG"}],
    })
    sr = soql_assert(fake, "row exists",
                     "SELECT FirstName FROM Contact WHERE Id='003x'",
                     expect={"FirstName": "Jane"})
    assert sr.status == "FAIL"

def test_soql_assert_fail_on_no_rows():
    sr = soql_assert(FakeSfClient(), "row exists",
                     "SELECT Id FROM Contact WHERE Id='nope'", expect={"Id": "x"})
    assert sr.status == "FAIL"

def test_vision_dispositions():
    import os
    from jsc_browser_tests.assertions.vision_checks import vision_step, disposition_for
    assert vision_step("v", "GEMINI_UNAVAILABLE").status == "SKIP"
    assert vision_step("v", "MODELS_EXHAUSTED").status == "SKIP"
    assert vision_step("v", "PARSE_FAIL").status == "WARN"
    assert vision_step("v", "STAGING_FAIL").status == "WARN"
    assert vision_step("v", "ERROR").status == "WARN"
    assert vision_step("v", "WARN").status == "WARN"
    assert vision_step("v", "OK").status == "PASS"
    assert vision_step("v", "SOMETHING_UNEXPECTED").status == "WARN"   # unknown → flag, never silently pass
    os.environ.pop("JSC_VISION_PROD_OK", None)
    assert disposition_for("PRODUCTION-BLOCKED") == "SKIP"
    os.environ["JSC_VISION_PROD_OK"] = "1"
    try:
        assert disposition_for("PRODUCTION-BLOCKED") == "PASS"
    finally:
        os.environ.pop("JSC_VISION_PROD_OK", None)
    # side_effects carries the raw vision status for evidence
    assert vision_step("v", "OK").side_effects["vision_status"] == "OK"

def main() -> int:
    failures = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try: fn(); print(f"PASS: {name}")
            except AssertionError as e: failures += 1; print(f"FAIL: {name}: {e}")
    print(f"\n{'ALL PASS' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(main())
