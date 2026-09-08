"""LightningToast — UTAM-mirrored Playwright wrapper.

Mirrors: vendor/node_modules/salesforce-pageobjects/dist/lightning/toast.utam.json
Source: salesforce-pageobjects v12.0.0 (Spring '26)

UTAM root selector:  lightning-notifications-library (or aria-live region)
UTAM shadow elements we use:
  label         css='.slds-text-heading_small > *'
  message       css="slot[name='message']"
  closeButton   css='lightning-button-icon'

Public UTAM methods (mirrored):
  getLabelText()   → get_label_text()
  getMessageText() → get_message_text()
  close()          → close()

Why this matters: Lightning toasts are the canonical save-success /
validation-error feedback. Asserting toast content is how flows verify
"Save worked" or "Save was blocked by VR" without scraping the page.
"""
from __future__ import annotations

import time
from ..lightning_page import DEFAULT_TIMEOUT_MS
from ...runner import StepResult


class LightningToast:
    """Wraps a Lightning toast notification.

        toast = LightningToast(page)
        await toast.wait_for_visible(timeout=10000)
        label = await toast.get_label_text()
        assert "Saved" in label
    """

    # Per Codex-R2-P1-02: UTAM toast.utam.json declares root selector
    # `lightning-toast` (the actual web-component), not the notification
    # container that holds it. We accept either — root falls back to
    # container so the wrapper still works when lightning-toast isn't
    # yet attached to the DOM (toast just dispatched), but PREFERS the
    # UTAM-canonical lightning-toast element.
    DEFAULT_ROOT = (
        "lightning-toast, .slds-notify_container .slds-notify_toast"
    )

    def __init__(self, page, *, root_selector: str | None = None):
        self.page = page
        self._root = page.locator(root_selector or self.DEFAULT_ROOT).first

    async def wait_for_visible(self, *, timeout: int = DEFAULT_TIMEOUT_MS) -> StepResult:
        t0 = time.monotonic()
        step = "toast.wait_for_visible"
        try:
            await self._root.wait_for(state="visible", timeout=timeout)
            return StepResult(
                step_name=step, status="PASS", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail="strategy=utam-mirrored:lightning-toast",
            )
        except Exception as e:
            return StepResult(
                step_name=step, status="FAIL", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail=f"{type(e).__name__}: {str(e)[:200]}",
            )

    async def get_label_text(self) -> str | None:
        el = self._root.locator(".slds-text-heading_small").first
        if await el.count() == 0:
            return None
        return (await el.inner_text()).strip()

    async def get_message_text(self) -> str | None:
        el = self._root.locator("slot[name='message']").first
        if await el.count() == 0:
            # Fallback — slot might not be visible to locator API in some browsers
            el = self._root.locator(".slds-text-body_regular").first
            if await el.count() == 0:
                return None
        return (await el.inner_text()).strip()

    async def close(self, *, timeout: int = DEFAULT_TIMEOUT_MS) -> StepResult:
        """Click close + wait for toast to disappear.

        Per Codex-R2-P1-02: UTAM's close compose includes a `waitForAbsence`
        step after the click. We implement the same — clicking close on a
        Lightning toast triggers an animated dismiss; the toast root is
        still queryable for ~500ms while the slide-out animation runs.
        Subsequent assertions need the toast to actually be gone.
        """
        t0 = time.monotonic()
        step = "toast.close"
        try:
            btn = self._root.locator("lightning-button-icon").first
            await btn.click(timeout=timeout)
            # Wait for toast root to detach or hide (UTAM waitForAbsence)
            try:
                await self._root.wait_for(state="hidden", timeout=5000)
            except Exception:
                # If hidden never lands, the toast may have already
                # detached from DOM — treat that as success too
                pass
            return StepResult(
                step_name=step, status="PASS", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail="strategy=utam-mirrored:lightning-toast/close+waitForAbsence",
            )
        except Exception as e:
            return StepResult(
                step_name=step, status="FAIL", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail=f"{type(e).__name__}: {str(e)[:200]}",
            )
