"""Logout As can return a Classic page without Lightning's identity provider."""
import asyncio

import pytest

from jsc_browser_tests import auth

ADMIN = "005000000000001AAA"
STAFF = "005000000000002AAA"
INSTANCE = "https://synthetic.invalid"


class ClassicLandingPage:
    def __init__(self, *, restored_user=ADMIN, link_available=True):
        self.user = STAFF
        self.lightning = True
        self.restored_user = restored_user
        self.link_available = link_available
        self.routes = []

    async def goto(self, url, **kwargs):
        self.routes.append(url)
        self.lightning = url.endswith("/lightning/page/home")

    async def wait_for_load_state(self, *args, **kwargs):
        pass

    async def wait_for_function(self, *args, **kwargs):
        if not self.lightning:
            raise TimeoutError("Classic has no Aura identity provider")

    async def evaluate(self, *args, **kwargs):
        return self.user if self.lightning else None

    def get_by_role(self, *args, **kwargs):
        page = self

        class Link:
            async def click(self, **kwargs):
                if not page.link_available:
                    raise TimeoutError("role locator unavailable")
                page.logout()

        return Link()

    def locator(self, selector):
        page = self
        assert selector == "a[href*='servlet.sulogout']"

        class Link:
            async def click(self, **kwargs):
                page.logout()

        return Link()

    def logout(self):
        self.user = self.restored_user
        self.lightning = False


@pytest.mark.parametrize("link_available", [True, False])
def test_classic_landing_returns_to_lightning_before_observing_original_user(link_available):
    page = ClassicLandingPage(link_available=link_available)
    result = asyncio.run(auth.restore_original_user(page, INSTANCE, ADMIN))
    assert result == {"status": "OBSERVED", "user_id": ADMIN, "action": "LOGOUT_AS"}
    assert page.routes == [INSTANCE + "/lightning/page/home"] * 2


def test_lightning_navigation_never_substitutes_for_restored_identity():
    page = ClassicLandingPage(restored_user=STAFF)
    with pytest.raises(auth.AuthError, match="did not restore the original browser user"):
        asyncio.run(auth.restore_original_user(page, INSTANCE, ADMIN))
