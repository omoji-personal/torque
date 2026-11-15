"""Login As and Logout As report what actually happened (offline, with page doubles).

- A browser that stays on the Login As address was not switched: say so and name the
  setting to check, instead of going on to a Lightning page that renders as the admin.
- A refused or aborted Login As is an authentication result, with the same checks.
- In Lightning the "Log out as" banner is found by its text when it is not a named link.
- A restore that was verified is not a preflight failure, whatever the click reported."""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jsc_browser_tests import auth, matrix
from jsc_browser_tests.runner import FlowResult
from jsc_browser_tests.sf_client import FakeSfClient
from jsc_browser_tests.suite import run_suite

ADMIN = "005000000000001AAA"
USER = "005000000000002AAA"
ORG = "00D000000000001AAA"
INSTANCE = "https://synthetic.invalid"


class ServletPage:
    """A page that records navigations. `lands` is where the Login As address leaves the browser."""

    def __init__(self, lands="stay", status=200, error=None):
        self.lands, self.status, self.error, self.routes, self.url = lands, status, error, [], ""

    async def goto(self, url, **kwargs):
        self.routes.append(url)
        if "/servlet/servlet.su" in url:
            if self.error:
                raise self.error
            self.url = url if self.lands == "stay" else INSTANCE + self.lands
            return SimpleNamespace(status=self.status)
        self.url = url
        return SimpleNamespace(status=200)

    async def wait_for_timeout(self, *args, **kwargs):
        pass

    async def wait_for_load_state(self, *args, **kwargs):
        pass


def login_as(page):
    return asyncio.run(auth.login_as_user(page, INSTANCE, USER, org_id_18=ORG))


def test_a_browser_that_stays_on_the_login_as_address_is_reported_with_the_setting_to_check():
    page = ServletPage(lands="stay")
    with pytest.raises(auth.AuthError) as raised:
        login_as(page)
    text = str(raised.value)
    assert "did not switch user" in text and "stayed on the Login As address" in text
    assert "Administrators Can Log in as Any User" in text and "Modify All Data" in text and "API-only" in text
    assert len(page.routes) == 1, "no Lightning page is opened as the admin afterwards"


def test_a_refused_login_as_names_the_http_status():
    page = ServletPage(lands="/_ui/classic/landing", status=500)
    with pytest.raises(auth.AuthError, match="HTTP 500") as raised:
        login_as(page)
    assert "Login Access Policies" in str(raised.value) and len(page.routes) == 1


def test_an_aborted_login_as_navigation_is_an_authentication_result():
    page = ServletPage(error=RuntimeError(f"net::ERR_ABORTED at {INSTANCE}/servlet/servlet.su?oid={ORG}"))
    with pytest.raises(auth.AuthError) as raised:
        login_as(page)
    assert "could not be opened (RuntimeError)" in str(raised.value) and "servlet.su?oid" not in str(raised.value)


def test_a_login_as_that_leaves_the_servlet_goes_on_to_lightning():
    page = ServletPage(lands="/_ui/classic/landing")
    login_as(page)
    assert page.routes == [INSTANCE + auth.login_as_path(ORG, USER), INSTANCE + "/lightning/page/home"]


class LightningBannerPage:
    """Lightning while logged in as a user: the banner is text, not a link with that
    accessible name, and there is no Classic logout link in the page."""

    def __init__(self):
        self.user, self.asked = USER, []

    async def goto(self, url, **kwargs):
        pass

    async def wait_for_load_state(self, *args, **kwargs):
        pass

    def get_by_role(self, role, name=None):
        self.asked.append("role")
        return SimpleNamespace(click=AsyncMock(side_effect=TimeoutError("no link with that name")))

    def get_by_text(self, text, exact=True):
        self.asked.append(f"text:{text}:{exact}")
        page = self

        async def click(**kwargs):
            page.user = ADMIN
        return SimpleNamespace(first=SimpleNamespace(click=click))

    def locator(self, selector):
        self.asked.append("href")
        return SimpleNamespace(click=AsyncMock(side_effect=TimeoutError("not in the Lightning DOM")))


def test_logout_as_finds_the_lightning_banner_by_its_text():
    page = LightningBannerPage()
    asyncio.run(auth.logout_as_user(page, INSTANCE))
    assert page.user == ADMIN
    assert page.asked == ["role", "text:Log out as:False"], "the Classic link is not needed in Lightning"


def test_logout_as_still_fails_plainly_when_nothing_is_found():
    page = LightningBannerPage()
    page.get_by_text = lambda *args, **kwargs: SimpleNamespace(
        first=SimpleNamespace(click=AsyncMock(side_effect=TimeoutError("no banner"))))
    with pytest.raises(auth.AuthError, match="Could not return from Login As"):
        asyncio.run(auth.logout_as_user(page, INSTANCE))


WARNED = {"status": "OBSERVED", "user_id": ADMIN, "action": "LOGOUT_AS",
          "action_warning": "TimeoutError: role locator unavailable"}


def test_a_verified_restore_is_not_a_preflight_failure(monkeypatch):
    monkeypatch.setattr(matrix, "observe_user_id", AsyncMock(side_effect=[ADMIN, USER]))
    monkeypatch.setattr(matrix, "login_as_user", AsyncMock())
    monkeypatch.setattr(matrix, "restore_original_user", AsyncMock(return_value=dict(WARNED)))
    admin = SimpleNamespace(instance_url=INSTANCE, org_id_18=ORG)
    result = asyncio.run(matrix.login_as_preflight(object(), {"users": {"standard": {"user_id": USER}}}, admin))
    assert result["standard"] == "PASS" and "preflight_error" not in result
    assert "original browser user is now observed" in result["preflight_warning"]
    assert "role locator unavailable" in result["preflight_warning"]


def test_an_unverified_restore_still_stops_the_preflight(monkeypatch):
    monkeypatch.setattr(matrix, "observe_user_id", AsyncMock(side_effect=[ADMIN, USER]))
    monkeypatch.setattr(matrix, "login_as_user", AsyncMock())
    monkeypatch.setattr(matrix, "restore_original_user",
                        AsyncMock(side_effect=auth.AuthError("Logout As did not restore the original browser user")))
    admin = SimpleNamespace(instance_url=INSTANCE, org_id_18=ORG)
    result = asyncio.run(matrix.login_as_preflight(object(), {"users": {"standard": {"user_id": USER}}}, admin))
    assert result["standard"] == "FAIL" and "Session restoration failed" in result["preflight_error"]


def test_a_preflight_warning_is_kept_in_the_manifest_and_the_cells_still_run(tmp_path):
    from jsc_browser_tests.library.smoke_login import FLOW as SMOKE
    flow = copy.deepcopy(SMOKE)
    flow.spec = copy.deepcopy(SMOKE.spec)
    flow.spec.profiles = ["admin", "standard"]
    executed = []

    async def preflight(config):
        return {"standard": "PASS", "preflight_warning": "standard: Logout As action reported an error, but ..."}

    async def executor(cell, config):
        executed.append(cell.profile)
        return FlowResult(flow_name=cell.flow.spec.name, profile=cell.profile, target_org="sf-fake",
                          overall_status="PASS")

    manifest_path = tmp_path / "manifest.json"
    config = {"sf": FakeSfClient(), "org_info": SimpleNamespace(detected_org_type="sandbox"), "flows": [flow],
              "profiles": ["admin", "standard"], "manifest_path": str(manifest_path), "run_dir": str(tmp_path),
              "audit_log": str(tmp_path / "audit.log"), "preflight": preflight, "cell_executor": executor,
              "seed": {"users": {"standard": {"user_id": USER}}}}
    asyncio.run(run_suite(config))
    assert executed == ["admin", "standard"], "a note about a verified restore does not stop the run"
    written = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "Logout As action reported an error" in written["preflight"]["preflight_warning"]
    assert all(cell["status"] == "PASS" for cell in written["cells"])
