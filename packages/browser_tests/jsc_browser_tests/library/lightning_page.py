"""lightning_page.py — Lightning-aware Playwright helper.

Two tiers, per design-v3 §3.3:

  TIER A — Thin core (session orchestration that UTAM JSON doesn't address)
    · goto_record(object_api, record_id)
    · goto_setup(setup_slug)
    · screenshot(name)
    · lightning_preflight() — standalone helper (not a method)
    Internal: _wait_lightning_ready()

  TIER B — Layer-2 fallback verbs (best-effort, Lightning-component-aware)
    · click_subtab(name)
    · fill_text(label, value)
    · click_button(name)
    · set_combobox(label, option)
    · dismiss_modal()
    Each emits fidelity=FIDELITY_LAYER_2_FALLBACK in its StepResult to make
    fallback usage visible in QA reports. Flows using these verbs are
    candidates for uplift to Layer-1 UTAM-mirrored wrappers (see
    lightning_components/ — added Phase 2 per the design spec) when
    salesforce-pageobjects ships a page object for the component.

  Long-term: Tier-A verbs stay forever (UTAM doesn't do session orchestration).
  Tier-B verbs shrink as Layer-1 wrappers absorb their use cases.

Companion: docs/superpowers/specs/2026-05-21-sf-browser-testing-playwright-mcp-design.md
(rationale). docs/sf-browser-testing-tools-handoff.md (plain-language operator guide).

Hard rules (codified here so flows can't violate them):
1. NEVER `wait_for_load_state("networkidle")` on Lightning — it polls forever.
   Use `wait_lightning_ready()` instead (waits for one-app-nav-bar + optional
   content hint).
2. NEVER use XPath locators against Lightning components (XPath does not
   pierce shadow DOM). Use role / text / data-* selectors.
3. NEVER click anything that triggers a native browser dialog (alert/confirm).
   Lightning custom modals are fine; native dialogs freeze the transport.
"""
from __future__ import annotations

import asyncio
import time
import urllib.parse
from pathlib import Path

from ..runner import StepResult


# Sentinels for selectors that prove Lightning shell is ready
LIGHTNING_SHELL_SELECTORS = (
    "one-app-nav-bar, .slds-global-header, lightning-vertical-navigation"
)
# Default per-verb timeout — operators can override per call
DEFAULT_TIMEOUT_MS = 15000
# Lightning shell can take 30+s on cold start; nav within a session 5-10s
LIGHTNING_READY_TIMEOUT_MS = 30000

# Fidelity markers for StepResult.fidelity — extends the existing
# {USER_FIDELITY, VF_ACTION_FUNCTION, BACKEND_DIAGNOSTIC} vocabulary
# defined in runner.py. Layer-1 (UTAM-mirrored wrappers in
# lightning_components/) emit USER_FIDELITY directly. Layer-2 fallback
# verbs in THIS module emit LAYER_2_FALLBACK so QA reports can flag
# flows that should be uplifted when Salesforce ships a UTAM page object
# for the component.
FIDELITY_USER = "USER_FIDELITY"
FIDELITY_LAYER_2_FALLBACK = "LAYER_2_FALLBACK"


class LightningPage:
    """Thin wrapper over a Playwright Page that codifies Lightning patterns.

    Construct with the live Page + instance URL + run_dir (where screenshots
    land). All verbs are async; each returns a StepResult.

        page = await context.new_page()
        lp = LightningPage(page, instance_url, run_dir)
        await lp.goto_record("Demo__Intake__c", record_id)
        await lp.click_subtab("Fields & Relationships")
        await lp.fill_text("Eligibility Reason", "Financial hardship")
        await lp.click_button("Save")
        await lp.screenshot("after_save")
    """

    def __init__(self, page, instance_url: str, run_dir: Path):
        self.page = page
        self.instance_url = instance_url.rstrip("/")
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)

    # ── Layer-1 component factories (UTAM-mirrored Playwright wrappers) ──
    # These return component wrappers scoped to the current page. Preferred
    # over Layer-2 fallback verbs when UTAM has a page object for the
    # target component. See lightning_components/__init__.py.

    def combobox(self, *, label=None, root_selector=None):
        from .lightning_components import LightningCombobox
        return LightningCombobox(self.page, label=label, root_selector=root_selector)

    def button(self, *, label=None, root_selector=None):
        from .lightning_components import LightningButton
        return LightningButton(self.page, label=label, root_selector=root_selector)

    def input_field(self, *, api_name=None, root_selector=None):
        from .lightning_components import LightningInputField
        return LightningInputField(self.page, api_name=api_name, root_selector=root_selector)

    def modal(self, *, root_selector=None):
        from .lightning_components import LightningModal
        return (
            LightningModal(self.page, root_selector=root_selector)
            if root_selector else LightningModal(self.page)
        )

    def toast(self, *, root_selector=None):
        from .lightning_components import LightningToast
        return LightningToast(self.page, root_selector=root_selector)

    def record_form(self, *, root_selector=None):
        from .lightning_components import LightningRecordForm
        return LightningRecordForm(self.page, root_selector=root_selector)

    # ── Internal helpers ─────────────────────────────────────────────────

    async def _wait_lightning_ready(
        self, hint_text: str | None = None, timeout_ms: int = LIGHTNING_READY_TIMEOUT_MS
    ) -> None:
        """Wait for Lightning shell to render. NEVER use networkidle.

        Rule 1 from this file's docstring: Lightning polls continuously, so
        networkidle never resolves. Wait for the app nav bar + (optional)
        a content-specific hint instead.
        """
        await self.page.wait_for_selector(
            LIGHTNING_SHELL_SELECTORS, state="visible", timeout=timeout_ms
        )
        if hint_text:
            try:
                hint = self.page.get_by_text(hint_text, exact=False).first
                await hint.wait_for(state="visible", timeout=timeout_ms)
            except Exception:
                # Hint is best-effort; shell visibility is the real gate
                pass

    async def _try_iframes(self, query_fn):
        """Walk every iframe + main frame to find an element.

        Setup pages render content inside iframes; the page-level locator
        often doesn't see toolbar buttons. Returns (frame, locator) or
        (None, None) if not found in any frame.
        """
        for fr in [self.page.main_frame] + list(self.page.frames):
            try:
                loc = query_fn(fr)
                if await loc.count() > 0:
                    return fr, loc
            except Exception:
                continue
        return None, None

    # ── Navigation verbs ─────────────────────────────────────────────────

    async def goto_record(self, object_api: str, record_id: str) -> StepResult:
        """Navigate to a Lightning record-view page + wait for shell.

        Args:
            object_api: e.g. "Demo__Intake__c", "Contact"
            record_id: 15- or 18-char Salesforce Id
        """
        t0 = time.monotonic()
        url = f"{self.instance_url}/lightning/r/{object_api}/{record_id}/view"
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await self._wait_lightning_ready()
            return StepResult(
                step_name=f"goto_record:{object_api}:{record_id[:6]}",
                status="PASS", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail=f"current URL: {self.page.url}",
            )
        except Exception as e:
            return StepResult(
                step_name=f"goto_record:{object_api}:{record_id[:6]}",
                status="FAIL", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail=f"{type(e).__name__}: {str(e)[:200]}",
            )

    async def goto_setup(self, setup_slug: str) -> StepResult:
        """Navigate to a Lightning Setup page + wait for shell.

        Args:
            setup_slug: e.g. "SetupOneHome/home",
                "ObjectManager/Demo__Intake__c/Details/view",
                "ManageUsers/home"
        """
        t0 = time.monotonic()
        url = f"{self.instance_url}/lightning/setup/{setup_slug.lstrip('/')}"
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await self._wait_lightning_ready()
            return StepResult(
                step_name=f"goto_setup:{setup_slug.split('/')[0]}",
                status="PASS", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail=f"current URL: {self.page.url}",
            )
        except Exception as e:
            return StepResult(
                step_name=f"goto_setup:{setup_slug.split('/')[0]}",
                status="FAIL", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail=f"{type(e).__name__}: {str(e)[:200]}",
            )

    # ── Interaction verbs ────────────────────────────────────────────────

    async def click_subtab(
        self, name: str, *, verify_url_contains: str | None = None
    ) -> StepResult:
        """Click a Lightning sub-tab by exact text match.

        Tries multiple locator strategies in real-user-fidelity order:
        get_by_role(link) → get_by_text(exact=True) → a[title].

        EMPIRICALLY: get_by_text(exact=True) is the most reliable for
        Lightning vertical-navigation items (sample-dev 2026-05-21).

        Args:
            name: exact label of the sub-tab, e.g. "Fields & Relationships"
            verify_url_contains: if set, wait for URL to change to contain
                this string (proves the click activated the sub-tab)
        """
        t0 = time.monotonic()
        step = f"click_subtab:{name}"

        strategies = [
            ("get_by_role(link)", lambda: self.page.get_by_role("link", name=name)),
            ("get_by_text(exact)",
             lambda: self.page.get_by_text(name, exact=True).first),
            ("a[title]",
             lambda: self.page.locator(f"a[title={name!r}]").first),
        ]
        used = None
        for label, builder in strategies:
            try:
                loc = builder()
                if await loc.count() > 0:
                    await loc.click(timeout=DEFAULT_TIMEOUT_MS)
                    used = label
                    break
            except Exception:
                continue

        if used is None:
            return StepResult(
                step_name=step, status="FAIL", fidelity=FIDELITY_LAYER_2_FALLBACK,
                duration_seconds=time.monotonic() - t0,
                detail=f"no locator strategy matched sub-tab {name!r}",
            )

        # Verify the click landed
        verify_detail = f"strategy={used}"
        if verify_url_contains:
            try:
                await self.page.wait_for_url(
                    f"**{verify_url_contains}**", timeout=DEFAULT_TIMEOUT_MS
                )
                verify_detail += f" + URL contains {verify_url_contains!r}"
            except Exception:
                return StepResult(
                    step_name=step, status="FAIL", fidelity=FIDELITY_LAYER_2_FALLBACK,
                    duration_seconds=time.monotonic() - t0,
                    detail=f"clicked via {used}, but URL did not contain {verify_url_contains!r}",
                )

        return StepResult(
            step_name=step, status="PASS", fidelity=FIDELITY_LAYER_2_FALLBACK,
            duration_seconds=time.monotonic() - t0, detail=verify_detail,
        )

    async def fill_text(self, label: str, value: str) -> StepResult:
        """Fill a text input identified by its visible label.

        Tries get_by_label, then placeholder, then walks iframes.
        Confirms the value landed via input_value().
        """
        t0 = time.monotonic()
        step = f"fill_text:{label}"

        # Strategy 1: get_by_label (Lightning form fields)
        strategies = [
            ("get_by_label", lambda fr: fr.get_by_label(label).first),
            ("get_by_placeholder", lambda fr: fr.get_by_placeholder(label).first),
        ]
        for sname, builder in strategies:
            try:
                # Try main frame first
                loc = builder(self.page)
                if await loc.count() > 0:
                    await loc.click(timeout=DEFAULT_TIMEOUT_MS)
                    await loc.fill(value)
                    landed = await loc.input_value()
                    if landed == value:
                        return StepResult(
                            step_name=step, status="PASS",
                            fidelity=FIDELITY_LAYER_2_FALLBACK,
                            duration_seconds=time.monotonic() - t0,
                            detail=f"strategy=main:{sname}",
                        )
            except Exception:
                pass

        # Walk iframes
        for sname, builder in strategies:
            fr, loc = await self._try_iframes(builder)
            if loc:
                try:
                    await loc.click(timeout=DEFAULT_TIMEOUT_MS)
                    await loc.fill(value)
                    landed = await loc.input_value()
                    if landed == value:
                        return StepResult(
                            step_name=step, status="PASS",
                            fidelity=FIDELITY_LAYER_2_FALLBACK,
                            duration_seconds=time.monotonic() - t0,
                            detail=f"strategy=iframe:{sname}",
                        )
                except Exception:
                    continue

        return StepResult(
            step_name=step, status="FAIL", fidelity=FIDELITY_LAYER_2_FALLBACK,
            duration_seconds=time.monotonic() - t0,
            detail=f"no input matched label {label!r} (main + iframes searched)",
        )

    async def click_button(
        self, name: str, *, wait_for_text: str | None = None
    ) -> StepResult:
        """Click a button by accessible name.

        Tries get_by_role("button") in main + iframes; also tries
        input[value=name] for classic VF-rendered buttons.

        Args:
            name: button text, e.g. "Save", "Edit", "New"
            wait_for_text: if set, wait for this text to appear after click
                (proves the action completed)
        """
        t0 = time.monotonic()
        step = f"click_button:{name}"

        strategies = [
            ("get_by_role(button)",
             lambda fr: fr.get_by_role("button", name=name).first),
            ("input[value]",
             lambda fr: fr.locator(f"input[value={name!r}]").first),
        ]

        clicked = False
        used = None
        for sname, builder in strategies:
            fr, loc = await self._try_iframes(builder)
            if loc is None:
                continue
            try:
                await loc.click(timeout=DEFAULT_TIMEOUT_MS)
                clicked = True
                used = sname
                break
            except Exception:
                continue

        if not clicked:
            return StepResult(
                step_name=step, status="FAIL", fidelity=FIDELITY_LAYER_2_FALLBACK,
                duration_seconds=time.monotonic() - t0,
                detail=f"no button matched name {name!r} (main + iframes searched)",
            )

        if wait_for_text:
            try:
                await self.page.get_by_text(
                    wait_for_text, exact=False
                ).first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)
            except Exception:
                return StepResult(
                    step_name=step, status="FAIL", fidelity=FIDELITY_LAYER_2_FALLBACK,
                    duration_seconds=time.monotonic() - t0,
                    detail=f"clicked via {used}, but {wait_for_text!r} did not appear",
                )

        return StepResult(
            step_name=step, status="PASS", fidelity=FIDELITY_LAYER_2_FALLBACK,
            duration_seconds=time.monotonic() - t0,
            detail=f"strategy={used}" + (
                f" + verified {wait_for_text!r} visible" if wait_for_text else ""
            ),
        )

    async def set_combobox(self, label: str, option: str) -> StepResult:
        """Set a Lightning lightning-combobox or classic select to `option`.

        Strategy: try lightning-combobox first (click → wait for listbox →
        click option), fall back to classic <select> (select_option by label),
        and walk iframes for both.
        """
        t0 = time.monotonic()
        step = f"set_combobox:{label}={option}"

        # Strategy A: classic <select> in any frame
        async def try_classic_select():
            for fr in [self.page.main_frame] + list(self.page.frames):
                try:
                    sel = fr.get_by_label(label).first
                    if await sel.count() > 0:
                        # Check it's a <select>
                        tag = await sel.evaluate("el => el.tagName")
                        if tag and tag.upper() == "SELECT":
                            await sel.select_option(label=option)
                            return True
                except Exception:
                    continue
            return False

        if await try_classic_select():
            return StepResult(
                step_name=step, status="PASS", fidelity=FIDELITY_LAYER_2_FALLBACK,
                duration_seconds=time.monotonic() - t0,
                detail="strategy=classic-select",
            )

        # Strategy B: Lightning lightning-combobox in main frame
        try:
            combo = self.page.get_by_label(label).first
            if await combo.count() > 0:
                await combo.click(timeout=DEFAULT_TIMEOUT_MS)
                # Listbox usually appears as role=listbox; option as role=option
                opt = self.page.get_by_role("option", name=option).first
                await opt.click(timeout=DEFAULT_TIMEOUT_MS)
                return StepResult(
                    step_name=step, status="PASS", fidelity=FIDELITY_LAYER_2_FALLBACK,
                    duration_seconds=time.monotonic() - t0,
                    detail="strategy=lightning-combobox",
                )
        except Exception:
            pass

        return StepResult(
            step_name=step, status="FAIL", fidelity=FIDELITY_LAYER_2_FALLBACK,
            duration_seconds=time.monotonic() - t0,
            detail=f"no combobox matched label {label!r} with option {option!r}",
        )

    async def dismiss_modal(self) -> StepResult:
        """Click Cancel / Close to dismiss a Lightning modal dialog.

        Tries Cancel first (most non-destructive), then Close, then × icon.
        Walks iframes since classic Setup modals render in iframes.
        """
        t0 = time.monotonic()
        step = "dismiss_modal"

        strategies = [
            ("button:Cancel",
             lambda fr: fr.get_by_role("button", name="Cancel").first),
            ("input[Cancel]",
             lambda fr: fr.locator("input[value='Cancel']").first),
            ("button:Close",
             lambda fr: fr.get_by_role("button", name="Close").first),
            ("button:Close this window",
             lambda fr: fr.get_by_role("button", name="Close this window").first),
        ]
        for sname, builder in strategies:
            fr, loc = await self._try_iframes(builder)
            if loc is None:
                continue
            try:
                await loc.click(timeout=DEFAULT_TIMEOUT_MS)
                return StepResult(
                    step_name=step, status="PASS", fidelity=FIDELITY_LAYER_2_FALLBACK,
                    duration_seconds=time.monotonic() - t0,
                    detail=f"strategy={sname}",
                )
            except Exception:
                continue

        return StepResult(
            step_name=step, status="FAIL", fidelity=FIDELITY_LAYER_2_FALLBACK,
            duration_seconds=time.monotonic() - t0,
            detail="no Cancel/Close button found in any frame",
        )

    async def screenshot(self, name: str, *, full_page: bool = False) -> StepResult:
        """Save a screenshot. Restrictive file permissions (0o600)."""
        t0 = time.monotonic()
        # Sanitize name (no path traversal, no special chars)
        safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
        if not safe_name.lower().endswith(".png"):
            safe_name += ".png"
        path = self.run_dir / safe_name
        try:
            await self.page.screenshot(path=str(path), full_page=full_page)
            # Restrict permissions — screenshots may contain PII per
            # qa-orchestration.md Vision surface posture
            try:
                import os
                os.chmod(path, 0o600)
            except Exception:
                pass
            return StepResult(
                step_name=f"screenshot:{safe_name}",
                status="PASS", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                screenshot_path=str(path),
                detail=f"size={path.stat().st_size} bytes",
            )
        except Exception as e:
            return StepResult(
                step_name=f"screenshot:{safe_name}",
                status="FAIL", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail=f"{type(e).__name__}: {str(e)[:200]}",
            )


# ── Preflight gate (per spec §3.4 + Codex-R1-P1-05) ─────────────────────


async def lightning_preflight(page, instance_url: str) -> StepResult:
    """5-second smoke test before any UAT session.

    Validates that:
      1. Frontdoor URL navigation works (no instance_url errors)
      2. Lightning shell loads (one-app-nav-bar visible)
      3. We didn't get redirected to the login page (auth handoff intact)

    Returns FAIL if any step fails — caller should abort the UAT session
    and surface the failure mode to the operator.
    """
    t0 = time.monotonic()
    try:
        url = f"{instance_url.rstrip('/')}/lightning/setup/SetupOneHome/home"
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_selector(
            LIGHTNING_SHELL_SELECTORS, state="visible", timeout=20000
        )
        # Verify not on login page
        current_url = page.url
        title = await page.title()
        if "login" in current_url.lower() or "login" in title.lower():
            return StepResult(
                step_name="lightning_preflight",
                status="FAIL", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail=f"redirected to login page (URL={current_url[:80]}, title={title[:40]}). "
                       "Auth handoff broke — try `sf org login web --alias <alias>`",
            )
        return StepResult(
            step_name="lightning_preflight",
            status="PASS", fidelity="USER_FIDELITY",
            duration_seconds=time.monotonic() - t0,
            detail=f"shell rendered; URL={current_url[:80]}",
        )
    except Exception as e:
        return StepResult(
            step_name="lightning_preflight",
            status="FAIL", fidelity="USER_FIDELITY",
            duration_seconds=time.monotonic() - t0,
            detail=f"{type(e).__name__}: {str(e)[:200]}. "
                   "Common causes: stale sf CLI auth, strict org MFA policy, "
                   "network. Try headed mode if headless is being blocked.",
        )


__all__ = [
    "LightningPage",
    "lightning_preflight",
    "LIGHTNING_SHELL_SELECTORS",
    "LIGHTNING_READY_TIMEOUT_MS",
    "DEFAULT_TIMEOUT_MS",
]
