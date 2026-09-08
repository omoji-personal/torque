"""Layer-1 UTAM-mirrored Playwright wrappers for Lightning components.

Each module in this package mirrors a single Lightning component's
selector strategy from the upstream `salesforce-pageobjects` npm package
(currently v12.0.0 = Spring '26 release). The JSON page-object files
live under `packages/browser_tests/vendor/node_modules/salesforce-pageobjects/dist/lightning/`.

Architecture:
  · We READ the UTAM JSON files at wrapper-authoring time
  · We MIRROR their selector strategies into Playwright wrappers we maintain
  · We do NOT depend on UTAM at runtime (no `utam` npm package install,
    no WebdriverIO, no subprocess bridge)

Refresh cadence:
  Quarterly per Salesforce release. Process:
    cd packages/browser_tests/vendor
    npm view salesforce-pageobjects dist-tags
    npm install --save-dev salesforce-pageobjects@<new-tag>
    git diff node_modules/salesforce-pageobjects/dist/lightning/
    # For each changed component file we wrap, update the wrapper to match

Per spec §3.4 in
docs/superpowers/specs/2026-05-21-sf-browser-testing-playwright-mcp-design.md.
"""
from __future__ import annotations

from .button import LightningButton
from .combobox import LightningCombobox
from .input_field import LightningInputField
from .modal import LightningModal
from .record_form import LightningRecordForm
from .toast import LightningToast

__all__ = [
    "LightningButton",
    "LightningCombobox",
    "LightningInputField",
    "LightningModal",
    "LightningRecordForm",
    "LightningToast",
]

# Anchor for refresh-cadence checks; bump with each `npm update` cycle
SALESFORCE_PAGEOBJECTS_VERSION = "12.0.0"
SALESFORCE_RELEASE = "Spring '26"
