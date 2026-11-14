"""The package's redaction removes every form of a sign-in address the Salesforce CLI (and
the endpoint it uses) can return: the front door with otp, sid or cshc, the single-access
endpoint, their percent-encoded forms, the JSON fields that carry them, and a one-time
value that turns up without its address. Synthetic values on reserved example domains."""
from __future__ import annotations

import json
from urllib.parse import quote

import pytest

from jsc_browser_tests.diagnostics import redact

CANARY = "CANARYsignin7Q"
HOST = "https://acme--sbx.sandbox.my.salesforce.example"
FORMS = {
    "frontdoor-otp": f"{HOST}/secur/frontdoor.jsp?otp={CANARY}&startURL=%2F",
    "frontdoor-otp-cshc": f"{HOST}/secur/frontdoor.jsp?otp=abc&cshc={CANARY}&startURL=%2F",
    "frontdoor-cshc-first": f"{HOST}/secur/frontdoor.jsp?cshc={CANARY}&otp=abc",
    "frontdoor-sid": f"{HOST}/secur/frontdoor.jsp?sid={CANARY}",
    "lightning-frontdoor": f"https://acme.lightning.force.example/secur/frontdoor.jsp?otp={CANARY}",
    "singleaccess-token": f"{HOST}/services/oauth2/singleaccess?token={CANARY}",
    "singleaccess-path": f"{HOST}/services/oauth2/singleaccess/{CANARY}",
}


@pytest.mark.parametrize("address", FORMS.values(), ids=FORMS.keys())
@pytest.mark.parametrize("encoded", [0, 1, 2], ids=["plain", "encoded", "encoded-twice"])
def test_every_sign_in_address_form_is_removed(address, encoded):
    for _ in range(encoded):
        address = quote(address, safe="")
    assert CANARY not in redact(f"navigation failed at {address} (timeout)")


@pytest.mark.parametrize("address", FORMS.values(), ids=FORMS.keys())
def test_the_clis_json_answer_is_redacted(address):
    answer = json.dumps({"status": 0, "result": {"url": address, "orgId": "00D000000000001AAA",
                                                 "username": "admin@acme.example"}})
    redacted = redact(answer)
    assert CANARY not in redacted and "admin@acme.example" in redacted


@pytest.mark.parametrize("key", ["frontdoor_uri", "frontdoorUri", "frontdoor_url", "cshc", "otp"])
def test_fields_that_carry_a_sign_in_value_are_redacted_by_name(key):
    assert redact({key: CANARY}) == {key: "[REDACTED]"}
    assert CANARY not in redact(json.dumps({key: CANARY}))
    assert CANARY not in redact(f"{key}={CANARY}")


def test_an_ordinary_address_is_left_alone():
    url = f"{HOST}/lightning/r/Contact/003000000000001AAA/view"
    assert redact(url) == url
