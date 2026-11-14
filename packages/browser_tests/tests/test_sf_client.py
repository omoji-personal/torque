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

def test_fake_frontdoor_and_identity():
    fake = FakeSfClient(
        frontdoor_url="https://x.my.salesforce.com/secur/frontdoor.jsp?sid=FAKE",
        org_id="00Dxx0000000001AAA", username="admin@x.example", is_sandbox=False,
    )
    assert "frontdoor.jsp" in fake.org_frontdoor_url("sf-test")
    assert fake.org_frontdoor("sf-test") == {"url": fake.org_frontdoor_url(), "org_id": "00Dxx0000000001AAA",
                                             "username": "admin@x.example"}
    assert fake.org_identity()["org_id"] == "00Dxx0000000001AAA" and fake.org_identity()["is_sandbox"] is False
    assert not hasattr(fake, "org_display"), "no `sf org display` stand-in: the real client makes no such call"


def test_admin_auth_comes_from_one_cli_answer():
    from jsc_browser_tests import auth
    fake = FakeSfClient(frontdoor_url="https://x.my.salesforce.com/secur/frontdoor.jsp?otp=FAKE&startURL=%2F",
                        org_id="00Dxx0000000001AAA", username="admin@x.example")
    admin = auth.get_admin_auth(fake, "sf-test")
    assert (admin.org_id_18, admin.instance_url, admin.username) == (
        "00Dxx0000000001AAA", "https://x.my.salesforce.com", "admin@x.example")
    fake._frontdoor_url = ""
    try:
        auth.get_admin_auth(fake, "sf-test")
    except auth.AuthError as error:
        assert "no url" in str(error)
    else:
        raise AssertionError("an answer without an address is not a sign-in")

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
