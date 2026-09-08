"""LightningRecordForm — UTAM-mirrored Playwright wrapper.

Mirrors: vendor/node_modules/salesforce-pageobjects/dist/lightning/recordForm.utam.json
Source: salesforce-pageobjects v12.0.0 (Spring '26)

UTAM root selector:  lightning-record-form (or lightning-record-edit-form)
UTAM shadow elements we use:
  cancelButton     css='.lightning-record-form-cancel'
  saveButton       css='.lightning-record-form-submit'
  spinner          css='lightning-spinner'
  inputFields      css='lightning-input-field'
  outputFields     css='lightning-output-field'

Public UTAM methods (mirrored):
  save()            → save()
  cancel()          → cancel()
  waitUntilReady()  → wait_until_ready()
  waitAndGetOutputFields() → get_output_fields()

Why this matters: dynamic-forms field sections render inside
lightning-record-form. The save/cancel buttons are scoped within the form
(not the page-level toolbar), which is why a generic click_button("Save")
can pick up the wrong Save in pages with multiple forms.
"""
from __future__ import annotations

import time
from ..lightning_page import DEFAULT_TIMEOUT_MS
from ...runner import StepResult


class LightningRecordForm:
    """Wraps a `lightning-record-form` (dynamic-forms field section).

        form = LightningRecordForm(page)
        await form.wait_until_ready()
        # ... fill fields via LightningInputField ...
        await form.save()
    """

    def __init__(self, page, *, root_selector: str | None = None):
        self.page = page
        # Accept either record-form (read+edit) or record-edit-form (edit only)
        self._root = page.locator(
            root_selector or "lightning-record-form, lightning-record-edit-form"
        ).first

    async def wait_until_ready(
        self, *, timeout: int = DEFAULT_TIMEOUT_MS
    ) -> StepResult:
        """Wait for spinner to disappear (form done loading initial data)."""
        t0 = time.monotonic()
        step = "record_form.wait_until_ready"
        try:
            spinner = self._root.locator("lightning-spinner").first
            # Spinner may or may not appear; both states are fine
            try:
                await spinner.wait_for(state="visible", timeout=2000)
                await spinner.wait_for(state="hidden", timeout=timeout)
            except Exception:
                # No spinner appeared — form already ready
                pass
            return StepResult(
                step_name=step, status="PASS", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail="strategy=utam-mirrored:lightning-record-form/spinner",
            )
        except Exception as e:
            return StepResult(
                step_name=step, status="FAIL", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail=f"{type(e).__name__}: {str(e)[:200]}",
            )

    async def save(self, *, timeout: int = DEFAULT_TIMEOUT_MS) -> StepResult:
        t0 = time.monotonic()
        step = "record_form.save"
        try:
            btn = self._root.locator(".lightning-record-form-submit").first
            await btn.scroll_into_view_if_needed(timeout=timeout)
            await btn.click(timeout=timeout)
            return StepResult(
                step_name=step, status="PASS", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail="strategy=utam-mirrored:lightning-record-form/submit",
            )
        except Exception as e:
            return StepResult(
                step_name=step, status="FAIL", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail=f"{type(e).__name__}: {str(e)[:200]}",
            )

    async def cancel(self, *, timeout: int = DEFAULT_TIMEOUT_MS) -> StepResult:
        t0 = time.monotonic()
        step = "record_form.cancel"
        try:
            btn = self._root.locator(".lightning-record-form-cancel").first
            await btn.click(timeout=timeout)
            return StepResult(
                step_name=step, status="PASS", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail="strategy=utam-mirrored:lightning-record-form/cancel",
            )
        except Exception as e:
            return StepResult(
                step_name=step, status="FAIL", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail=f"{type(e).__name__}: {str(e)[:200]}",
            )

    def get_output_fields(self):
        """Return a Locator covering all rendered output-field elements."""
        return self._root.locator("lightning-output-field")

    def get_input_fields(self):
        """Return a Locator covering all rendered input-field elements."""
        return self._root.locator("lightning-input-field")
