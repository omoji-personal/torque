"""LightningCombobox — UTAM-mirrored Playwright wrapper.

Mirrors: vendor/node_modules/salesforce-pageobjects/dist/lightning/combobox.utam.json
Source: salesforce-pageobjects v12.0.0 (Spring '26)

UTAM root selector:  lightning-combobox
UTAM shadow elements we use:
  labelText        css='label'
  required         css='.slds-required'         (inside labelText)
  base             css='lightning-base-combobox'

Public UTAM methods (mirrored as Python methods):
  getLabelText()    → get_label_text()
  isRequired()      → is_required()
  openDropdown()    → open_dropdown()  (clicks the base combobox to expand)
  hasError()        → has_error()
  getErrorMessage() → get_error_message()

Custom additions (not in UTAM, useful in Playwright):
  select_option(option_text)  — open dropdown + click option in listbox
"""
from __future__ import annotations

import time
from ..lightning_page import DEFAULT_TIMEOUT_MS
from ...runner import StepResult


class LightningCombobox:
    """Wraps a `lightning-combobox` instance scoped by label OR explicit root.

        # By visible label (most common)
        cb = LightningCombobox(page, label="Status")
        await cb.select_option("Closed")

        # By explicit root selector (when you've already located it)
        cb = LightningCombobox(page, root_selector='lightning-combobox[data-id="status"]')
    """

    def __init__(self, page, *, label: str | None = None, root_selector: str | None = None):
        if not label and not root_selector:
            raise ValueError("LightningCombobox needs either label= or root_selector=")
        self.page = page
        self.label = label
        if root_selector:
            self._root = page.locator(root_selector).first
        else:
            # Locate by label text within a lightning-combobox
            self._root = page.locator(
                f"lightning-combobox:has(label:text-is({label!r}))"
            ).first

    async def get_label_text(self) -> str:
        return (await self._root.locator("label").first.inner_text()).strip()

    async def is_required(self) -> bool:
        return await self._root.locator(".slds-required").first.count() > 0

    async def open_dropdown(self, timeout: int = DEFAULT_TIMEOUT_MS) -> None:
        """Click the base combobox to expand the option listbox."""
        await self._root.locator("lightning-base-combobox").first.click(timeout=timeout)

    async def has_error(self) -> bool:
        # Per Codex-R2-P1-04: UTAM combobox.utam.json declares hasError via
        # the errorHiddenLabel element at "[data-help-text] .slds-assistive-text",
        # NOT just "[data-help-text]" (which is the error message container).
        # The assistive-text presence is what indicates the field is in an error
        # state — the [data-help-text] container also renders for helptext.
        return await self._root.locator(
            "[data-help-text] .slds-assistive-text"
        ).first.count() > 0

    async def get_error_message(self) -> str | None:
        err = self._root.locator("[data-help-text]").first
        if await err.count() == 0:
            return None
        return (await err.inner_text()).strip()

    async def select_option(
        self, option_text: str, *, timeout: int = DEFAULT_TIMEOUT_MS
    ) -> StepResult:
        """Open dropdown, click option matching `option_text`, return StepResult."""
        t0 = time.monotonic()
        step = f"combobox.select_option:{self.label or '?'}={option_text}"
        try:
            await self.open_dropdown(timeout=timeout)
            # Listbox renders as role=listbox with role=option children
            opt = self.page.get_by_role("option", name=option_text).first
            await opt.click(timeout=timeout)
            return StepResult(
                step_name=step, status="PASS", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail="strategy=utam-mirrored:lightning-combobox",
            )
        except Exception as e:
            return StepResult(
                step_name=step, status="FAIL", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail=f"{type(e).__name__}: {str(e)[:200]}",
            )
