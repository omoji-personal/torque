"""LightningInputField — UTAM-mirrored Playwright wrapper.

Mirrors: vendor/node_modules/salesforce-pageobjects/dist/lightning/inputField.utam.json
Source: salesforce-pageobjects v12.0.0 (Spring '26)

UTAM root selector:  lightning-input-field
UTAM shadow elements we use:
  input          css='lightning-input'           — text / number / email
  inputAddress   css='lightning-input-address'   — address group
  inputName      css='lightning-input-name'      — Name compound field
  picklist       css='lightning-picklist'        — picklist
  textArea       css='lightning-textarea'        — Long Text Area
  lookup         css='lightning-lookup'          — lookup reference field

`lightning-input-field` is the wrapper Salesforce uses for record-form fields.
It internally renders one of the above based on the bound SObject field type.

Public UTAM methods (mirrored):
  waitAndGetInput()     → get_input()
  waitAndGetLookup()    → get_lookup()
  waitAndGetTextarea()  → get_textarea()
  waitAndGetPicklist()  → get_picklist()

Custom additions (not in UTAM):
  set_value(value)  — type into the inner input (works for text/number/email)
"""
from __future__ import annotations

import time
from ..lightning_page import DEFAULT_TIMEOUT_MS
from ...runner import StepResult


class LightningInputField:
    """Wraps a `lightning-input-field` by API name OR explicit root selector.

        # By API field name (Salesforce-canonical)
        field = LightningInputField(page, api_name="Eligibility_Reason__c")
        await field.set_value("Financial hardship")

        # By root selector (e.g. nth instance on the form)
        field = LightningInputField(page, root_selector='lightning-input-field:nth-of-type(3)')
    """

    def __init__(self, page, *, api_name: str | None = None, root_selector: str | None = None):
        if not api_name and not root_selector:
            raise ValueError("LightningInputField needs either api_name= or root_selector=")
        self.page = page
        self.api_name = api_name
        if root_selector:
            self._root = page.locator(root_selector).first
        else:
            self._root = page.locator(
                f"lightning-input-field[field-name={api_name!r}]"
            ).first

    def get_input(self):
        return self._root.locator("lightning-input").first

    def get_lookup(self):
        return self._root.locator("lightning-lookup").first

    def get_textarea(self):
        return self._root.locator("lightning-textarea").first

    def get_picklist(self):
        return self._root.locator("lightning-picklist").first

    async def set_value(
        self, value: str, *, timeout: int = DEFAULT_TIMEOUT_MS
    ) -> StepResult:
        """Type `value` into the inner input (text / number / email types)."""
        t0 = time.monotonic()
        step = f"input_field.set_value:{self.api_name or '?'}={value!r}"
        try:
            inp = self.get_input().locator("input").first
            await inp.click(timeout=timeout)
            await inp.fill(value)
            landed = await inp.input_value()
            if landed == value:
                return StepResult(
                    step_name=step, status="PASS", fidelity="USER_FIDELITY",
                    duration_seconds=time.monotonic() - t0,
                    detail="strategy=utam-mirrored:lightning-input-field/input",
                )
            return StepResult(
                step_name=step, status="FAIL", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail=f"value did not land: expected {value!r}, got {landed!r}",
            )
        except Exception as e:
            return StepResult(
                step_name=step, status="FAIL", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail=f"{type(e).__name__}: {str(e)[:200]}",
            )
