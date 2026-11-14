"""A spinner or a toast that never goes away is not a passed step.

Before, the form's readiness wait and the toast's close both reported PASS when the wait
for "hidden" failed: a stalled form or an undismissed notification looked verified."""
import asyncio

import pytest

from jsc_browser_tests.library.lightning_components.record_form import LightningRecordForm
from jsc_browser_tests.library.lightning_components.toast import LightningToast


class Element:
    """A locator double. `appears`: it becomes visible; `leaves`: it becomes hidden."""

    def __init__(self, appears=True, leaves=True, visible_after=None):
        self.appears, self.leaves, self.waits = appears, leaves, []
        self.visible_after = (not leaves) if visible_after is None else visible_after
        self.first = self

    def locator(self, selector):
        return self

    async def wait_for(self, state, timeout):
        self.waits.append((state, timeout))
        if (state == "visible" and not self.appears) or (state == "hidden" and not self.leaves):
            raise TimeoutError(f"Timeout {timeout}ms exceeded waiting for {state}")

    async def click(self, **kwargs):
        pass

    async def is_visible(self):
        if self.visible_after == "unknown":
            raise RuntimeError("the page is gone")
        return self.visible_after


class Page:
    def __init__(self, element):
        self.element = element

    def locator(self, selector):
        return self.element


def ready(element, **options):
    return asyncio.run(LightningRecordForm(Page(element)).wait_until_ready(**options))


def test_no_spinner_means_the_form_was_ready():
    assert ready(Element(appears=False)).status == "PASS"


def test_a_spinner_that_appears_and_hides_is_ready():
    element = Element(appears=True, leaves=True)
    assert ready(element, timeout=4000).status == "PASS"
    assert element.waits == [("visible", 2000), ("hidden", 4000)]


def test_a_spinner_that_stays_visible_is_a_failed_step():
    result = ready(Element(appears=True, leaves=False), timeout=4000)
    assert result.status == "FAIL"
    assert "spinner was still visible after 4000 ms" in result.detail


def close(element, **options):
    return asyncio.run(LightningToast(Page(element)).close(**options))


def test_a_toast_that_goes_away_is_closed():
    element = Element(leaves=True)
    assert close(element, timeout=3000).status == "PASS"
    assert element.waits == [("hidden", 3000)], "the caller's timeout is used, not a fixed one"


def test_a_toast_whose_wait_failed_but_is_observed_gone_is_closed():
    assert close(Element(leaves=False, visible_after=False)).status == "PASS"


@pytest.mark.parametrize("after", [True, "unknown"])
def test_a_toast_that_is_still_there_is_a_failed_step(after):
    result = close(Element(leaves=False, visible_after=after), timeout=3000)
    assert result.status == "FAIL" and "toast was still visible after close" in result.detail
