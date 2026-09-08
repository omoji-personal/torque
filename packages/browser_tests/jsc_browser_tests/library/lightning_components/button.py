"""LightningButton — UTAM-mirrored Playwright wrapper.

Mirrors: vendor/node_modules/salesforce-pageobjects/dist/lightning/button.utam.json
Source: salesforce-pageobjects v12.0.0 (Spring '26)

UTAM root selector:  lightning-button
UTAM shadow elements:
  button  (the inner <button>; selected via lightning-button > button)

Public UTAM methods (mirrored):
  click()           → click()        — scrollIntoView + click
  focus()           → focus()
  getButtonName()   → get_button_name()
  isDisabled()      → is_disabled()
"""
from __future__ import annotations

import time
from ..lightning_page import DEFAULT_TIMEOUT_MS
from ...runner import StepResult


class LightningButton:
    """Wraps a `lightning-button` by visible label OR explicit root selector.

        btn = LightningButton(page, label="Save")
        result = await btn.click()
    """

    def __init__(self, page, *, label: str | None = None, root_selector: str | None = None):
        if not label and not root_selector:
            raise ValueError("LightningButton needs either label= or root_selector=")
        self.page = page
        self.label = label
        if root_selector:
            self._root = page.locator(root_selector).first
        else:
            # Locate by accessible button name; Playwright's role-selector
            # walks the accessibility tree so this also matches when the
            # text is in a slotted child
            self._root = page.locator(
                f"lightning-button:has(button:text-is({label!r}))"
            ).first
            # Fallback: ARIA-role match (handles slotted text)
            self._fallback = page.get_by_role("button", name=label).first

    async def click(self, *, timeout: int = DEFAULT_TIMEOUT_MS) -> StepResult:
        """Scroll-into-view + click the inner <button> (UTAM .compose path)."""
        t0 = time.monotonic()
        step = f"button.click:{self.label or '?'}"
        try:
            target = self._root.locator("button").first
            if await target.count() == 0:
                # Fallback when no lightning-button wrapper present
                target = self._fallback
            await target.scroll_into_view_if_needed(timeout=timeout)
            await target.click(timeout=timeout)
            return StepResult(
                step_name=step, status="PASS", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail="strategy=utam-mirrored:lightning-button",
            )
        except Exception as e:
            return StepResult(
                step_name=step, status="FAIL", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail=f"{type(e).__name__}: {str(e)[:200]}",
            )

    async def focus(self) -> None:
        target = self._root.locator("button").first
        if await target.count() == 0:
            target = self._fallback
        await target.focus()

    async def get_button_name(self) -> str:
        target = self._root.locator("button").first
        if await target.count() == 0:
            target = self._fallback
        return (await target.inner_text()).strip()

    async def is_disabled(self) -> bool:
        target = self._root.locator("button").first
        if await target.count() == 0:
            target = self._fallback
        return await target.is_disabled()
