"""An object of the browser package that holds a sign-in address, or a Playwright object
whose repr names the page's address, never shows it in its repr or str: an accidental
print, log line or traceback of it cannot leak the address. Made-up values only."""
from __future__ import annotations

from pathlib import Path

import pytest

from jsc_browser_tests import auth, relay
from jsc_browser_tests.flow_spec import FlowCtx
from jsc_browser_tests.runner import Variation

CANARY = "CANARYreprNEVERshown"
ADDRESS = f"https://acme.my.salesforce.example/secur/frontdoor.jsp?otp={CANARY}&startURL=%2F"


class PlaywrightObject:
    """Stands in for a Playwright Page, Browser or Context: their repr names the address."""

    def __repr__(self):
        return f"<Page url={ADDRESS!r}>"


def admin_auth():
    return auth.AdminAuth("acme-sbx", "00D000000000001AAA", "https://acme.my.salesforce.example", ADDRESS,
                          username="admin@acme.example", api_version="62.0")


HOLDERS = {
    "AdminAuth": admin_auth,
    "Minted": lambda: relay.Minted(ADDRESS, "00D000000000001AAA", "admin@acme.example"),
    "Outcome": lambda: relay.Outcome("redirected", "", relay.Minted(ADDRESS)),
    "BrowserSession": lambda: auth.BrowserSession(page=PlaywrightObject(), channel="frontdoor",
                                                  browser=PlaywrightObject(), context=PlaywrightObject(),
                                                  owns_browser=True),
    "FlowCtx": lambda: FlowCtx("acme-sbx", "https://acme.my.salesforce.example", "00D000000000001AAA", "r1",
                               Path("."), Variation("v", "admin"), "admin", {}, {}, {}, PlaywrightObject(), None),
}


@pytest.mark.parametrize("make", HOLDERS.values(), ids=HOLDERS.keys())
def test_repr_and_str_never_show_the_address(make):
    held = make()
    for text in (repr(held), str(held), f"{held}", "%s" % (held,), repr([held])):
        assert CANARY not in text


def test_admin_auth_still_works_as_before():
    admin = admin_auth()
    assert admin.frontdoor_url == ADDRESS and admin.username == "admin@acme.example"
    assert "acme-sbx" in repr(admin) and "[hidden]" in repr(admin)
    assert admin._replace(username="other@acme.example").frontdoor_url == ADDRESS
