"""LightningModal — UTAM-mirrored Playwright wrapper.

Mirrors: vendor/node_modules/salesforce-pageobjects/dist/lightning/modal.utam.json
Source: salesforce-pageobjects v12.0.0 (Spring '26)

UTAM root selector:  lightning-modal
UTAM shadow elements we use:
  modal         css='lightning-modal'        (root itself)
  closeButton   css='[data-close-button]'
  ariaLabel     css='[data-modal]'

Public UTAM methods (mirrored):
  close()                 → close()              — click close button
  getHeaderlessLabel()    → get_headerless_label()
  getDescription()        → get_description()

Note: there's a separate `lightning/modalFooter.utam.json` for footer actions.
Use LightningModal for the dialog wrapper; use LightningButton for footer buttons.
"""
from __future__ import annotations

import time
from ..lightning_page import DEFAULT_TIMEOUT_MS
from ...runner import StepResult


class LightningModal:
    """Wraps a `lightning-modal` — the wrapper element, not its inner content.

        modal = LightningModal(page)
        if await modal.is_visible():
            await modal.close()
    """

    # Per Codex-R2-P1-03: UTAM modal.utam.json's `description` field declares
    # "Selector: lightning-modal-base" — the base web-component, not the
    # subclassed lightning-modal. The UTAM `shadow.elements` then references
    # the inner `lightning-modal` as a NESTED element. We default to
    # lightning-modal-base; callers can override if they know the modal is
    # an unwrapped lightning-modal in their context.
    def __init__(self, page, *, root_selector: str = "lightning-modal-base"):
        self.page = page
        self._root = page.locator(root_selector).first

    async def is_visible(self) -> bool:
        return await self._root.count() > 0 and await self._root.is_visible()

    async def close(self, *, timeout: int = DEFAULT_TIMEOUT_MS) -> StepResult:
        """Click the close button (the × in the modal header)."""
        t0 = time.monotonic()
        step = "modal.close"
        try:
            btn = self._root.locator("[data-close-button]").first
            await btn.click(timeout=timeout)
            return StepResult(
                step_name=step, status="PASS", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail="strategy=utam-mirrored:lightning-modal/[data-close-button]",
            )
        except Exception as e:
            return StepResult(
                step_name=step, status="FAIL", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail=f"{type(e).__name__}: {str(e)[:200]}",
            )

    async def get_headerless_label(self) -> str | None:
        el = self._root.locator("[data-modal]").first
        if await el.count() == 0:
            return None
        return await el.get_attribute("aria-label")

    async def get_description(self) -> str | None:
        # Per Codex-R2-P1-03: UTAM modal.utam.json's getDescription compose
        # path is element=ariaDescription ([data-modal]), apply=getAttribute,
        # args=['aria-description']. We implement the SAME — read the
        # aria-description attribute off the [data-modal] element rather
        # than the inner_text of [data-aria-description] (which is a
        # DIFFERENT element — the describedBy target).
        el = self._root.locator("[data-modal]").first
        if await el.count() == 0:
            return None
        return await el.get_attribute("aria-description")

    async def get_described_by(self) -> str | None:
        # Per Codex-R2-P1-03: this is what the previous get_description
        # implementation was actually doing. Renamed + kept for the case
        # where the modal uses aria-describedby pointing at a separate
        # descriptive text element.
        el = self._root.locator("[data-aria-description]").first
        if await el.count() == 0:
            return None
        return (await el.inner_text()).strip()
