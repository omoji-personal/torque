"""Torque's scripted runners ask for the single-use sign-in address only once Playwright
runs, just before the browser is launched and sent to it, as `torque browser login
--storage-state` does: a Playwright that cannot start leaves no address asked for."""
from __future__ import annotations

import asyncio
import sys
import types

import pytest

from jsc_browser_tests import auth, runner, suite

USER = "005000000000002AAA"


@pytest.fixture
def order(monkeypatch):
    for name in ("DEBUG", "PWDEBUG", "DEBUG_FILE", "TORQUE_BROWSER_CDP", "JSC_BROWSER_CDP"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(auth, "in_connected_mode", lambda: False)
    events = []

    class Playwright:
        async def __aenter__(self):
            events.append("playwright started")
            return object()

        async def __aexit__(self, *args):
            events.append("playwright stopped")

    api = types.ModuleType("playwright.async_api")
    api.async_playwright = Playwright
    monkeypatch.setitem(sys.modules, "playwright.async_api", api)

    def mint(*args, **kwargs):
        events.append("address asked for")
        return auth.AdminAuth("acme-dev", "00D000000000003AAA", "https://acme-dev.my.salesforce.example",
                              "https://acme-dev.my.salesforce.example/secur/frontdoor.jsp?otp=CANARY")

    async def open_session(*args, **kwargs):
        events.append("browser opened")
        raise auth.AuthError("stop here")

    monkeypatch.setattr(auth, "get_admin_auth", mint)
    monkeypatch.setattr(auth, "open_session", open_session)
    return events


def test_the_flow_runner_asks_for_the_address_after_playwright_starts(order, tmp_path):
    flow = types.SimpleNamespace(name="visit")
    asyncio.run(runner.run_flow_variation(flow, runner.Variation("v", "admin"), "acme-dev", tmp_path,
                                          sf_client=object()))
    assert order[:3] == ["playwright started", "address asked for", "browser opened"]


def test_the_preflight_asks_for_the_address_after_playwright_starts(order):
    config = {"profiles": ["admin", "standard"], "seed": {"users": {"standard": {"user_id": USER}}},
              "sf": object(), "target_org": "acme-dev"}
    with pytest.raises(auth.AuthError):
        asyncio.run(suite._live_preflight(config))
    assert order[:3] == ["playwright started", "address asked for", "browser opened"]
