from __future__ import annotations
import sys
from pathlib import Path
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jsc_browser_tests.sf_client import FakeSfClient

def test_fake_query_returns_canned_rows():
    fake = FakeSfClient(query_results={
        "SELECT Id FROM Contact WHERE Id='003x'": [{"Id": "003x", "FirstName": "Jane"}],
    })
    rows = fake.query("SELECT Id FROM Contact WHERE Id='003x'")
    assert rows == [{"Id": "003x", "FirstName": "Jane"}]

def test_fake_query_unknown_query_returns_empty():
    fake = FakeSfClient()
    assert fake.query("SELECT Id FROM Account") == []

def test_fake_records_apex_calls():
    fake = FakeSfClient()
    fake.apex_run("insert new Account(Name='TEST');")
    assert fake.apex_calls == ["insert new Account(Name='TEST');"]

def test_fake_frontdoor_and_display():
    fake = FakeSfClient(
        frontdoor_url="https://x.my.salesforce.com/secur/frontdoor.jsp?sid=FAKE",
        org_display={"id": "00Dxx0000000001", "instanceUrl": "https://x.my.salesforce.com"},
    )
    assert "frontdoor.jsp" in fake.org_frontdoor_url("sf-test")
    assert fake.org_display("sf-test")["id"] == "00Dxx0000000001"

def main() -> int:
    failures = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS: {name}")
            except AssertionError as e:
                failures += 1; print(f"FAIL: {name}: {e}")
    print(f"\n{'ALL PASS' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(main())
