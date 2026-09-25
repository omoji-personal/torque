"""runner.py — execute browser test flows (library or ad-hoc).

Engine: Playwright Python (consolidates 3 fragmented systems per design-v4).
For ad-hoc Claude-driven exploration, recommend Chrome DevTools MCP per
feedback_browser_testing_tool.md memory.

Per design-v4 Closure 5: 3-tier VF fidelity ladder for cascading picklist robustness.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import auth


@dataclass
class StepResult:
    step_name: str
    status: str  # 'PASS' | 'FAIL' | 'SKIP' | 'NOT_APPLICABLE' | 'WARN'
    fidelity: str  # 'USER_FIDELITY' | 'VF_ACTION_FUNCTION' | 'BACKEND_DIAGNOSTIC' | 'LAYER_2_FALLBACK'
    duration_seconds: float = 0.0
    detail: str = ""
    screenshot_path: str | None = None
    side_effects: dict = field(default_factory=dict)  # SOQL/vision evidence per step


@dataclass
class FlowResult:
    flow_name: str
    profile: str  # 'admin' | 'standard' | 'platform' | etc.
    target_org: str
    overall_status: str  # 'PASS' | 'FAIL'
    steps: list[StepResult] = field(default_factory=list)
    duration_seconds: float = 0.0
    side_effects: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class Variation:
    """One execution variant of a flow (happy / missing-field / wrong-field / perm-denial)."""
    name: str
    profile: str = "admin"  # which test-user role
    inputs: dict = field(default_factory=dict)
    expect: str = "success"  # 'success' OR 'error: <message>'


class BaseFlow:
    """Base class for library flows (gold-suite contract).

    Subclasses set a `spec` (FlowSpec) and implement:
      async def run(self, page, ctx, variation) -> list[StepResult]
      async def verify_side_effects(self, ctx) -> dict      (optional)
      async def provision(self, ctx) -> dict                 (optional; returns handles)
      async def teardown(self, ctx) -> None                  (optional)
    `name` defaults to spec.name when a spec is present.
    """
    spec = None  # set by subclass: FlowSpec(...)
    name: str = "BaseFlow"
    variations: list[Variation] = []

    async def provision(self, ctx) -> dict:
        return {}

    async def run(self, page, ctx, variation: Variation) -> list[StepResult]:
        raise NotImplementedError("subclass must implement run()")

    async def verify_side_effects(self, ctx) -> dict:
        return {}

    async def teardown(self, ctx) -> None:
        return None


# ── 3-tier VF fidelity ladder helpers (per design-v4 Closure 10) ──────────


async def vf_input_with_fallback(
    page,
    locator_str: str,
    value: str,
    *,
    action_function_js: str | None = None,
    apex_diagnostic_fn: Callable | None = None,
    target_org: str | None = None,
) -> StepResult:
    """Try to set a VF input via 3-tier fidelity ladder.

    Tier 1 — Real UI: page.locator(locator_str).fill(value)
    Tier 2 — actionFunction: page.evaluate(action_function_js)
    Tier 3 — Backend Apex (LABEL EXPLICITLY as BACKEND_DIAGNOSTIC, NOT browser PASS)

    Returns StepResult with fidelity field reflecting which tier succeeded.
    """
    t0 = time.monotonic()

    # Per audit gemini-R4: Python 3 deletes except-bound variables at the end
    # of the except block, so referencing `ui_err` in Tier 2/3 below would
    # raise UnboundLocalError if Tier 1 failed. Capture the message OUTSIDE
    # the except via an outer variable.
    ui_err_msg = ""
    # Tier 1
    try:
        loc = page.locator(locator_str)
        await loc.wait_for(state="visible", timeout=5000)
        tag = await loc.evaluate("el => el.tagName")
        if tag == "SELECT":
            await loc.select_option(value=value, timeout=5000)
        else:
            await loc.fill(value, timeout=5000)
        return StepResult(
            step_name=f"input {locator_str}",
            status="PASS", fidelity="USER_FIDELITY",
            duration_seconds=time.monotonic() - t0,
            detail=f"set value={value!r} via real UI",
        )
    except Exception as ui_err:
        ui_err_msg = f"{type(ui_err).__name__}: {ui_err}"

    # Tier 2 — actionFunction (if caller provided JS)
    if action_function_js:
        try:
            await page.evaluate(action_function_js)
            return StepResult(
                step_name=f"input {locator_str}",
                status="PASS", fidelity="VF_ACTION_FUNCTION",
                duration_seconds=time.monotonic() - t0,
                detail=f"set value={value!r} via VF actionFunction (UI tier failed: {ui_err_msg})",
            )
        except Exception as af_err:
            ui_err_msg = f"{ui_err_msg}; actionFunction also failed: {type(af_err).__name__}: {af_err}"

    # Tier 3 — Backend Apex (LABEL EXPLICITLY)
    if apex_diagnostic_fn and target_org:
        try:
            await apex_diagnostic_fn(target_org, value)
            return StepResult(
                step_name=f"input {locator_str}",
                status="PASS", fidelity="BACKEND_DIAGNOSTIC",
                duration_seconds=time.monotonic() - t0,
                detail=(
                    f"set value={value!r} via Apex diagnostic — "
                    f"NOT a browser PASS, only backend coverage. UI failed: {ui_err_msg}"
                ),
            )
        except Exception as apex_err:
            return StepResult(
                step_name=f"input {locator_str}",
                status="FAIL", fidelity="USER_FIDELITY",
                duration_seconds=time.monotonic() - t0,
                detail=f"all 3 tiers failed. UI: {ui_err_msg}; Apex: {type(apex_err).__name__}: {apex_err}",
            )

    return StepResult(
        step_name=f"input {locator_str}",
        status="FAIL", fidelity="USER_FIDELITY",
        duration_seconds=time.monotonic() - t0,
        detail=f"UI tier failed and no fallback configured: {ui_err_msg}",
    )


# ── Cascading picklist helper from Phase 1 lesson ─────────────────────────


async def set_cascading_picklist(
    page,
    parent_locator: str,
    parent_value: str,
    child_locator: str,
    child_value: str,
    wait_for_ajax_ms: int = 1500,
) -> tuple[bool, str]:
    """Set a parent picklist + wait for child AJAX + set child.

    Adopts Phase 1's JS-based event-dispatch helper as canonical pattern.
    Returns (success, detail).
    """
    try:
        parent_loc = page.locator(parent_locator)
        await parent_loc.wait_for(state="visible", timeout=5000)

        # Use JS to set value + dispatch change event (more reliable than .select_option)
        await parent_loc.evaluate(
            "(el, v) => { el.value = v; "
            "el.dispatchEvent(new Event('change', {bubbles: true})); }",
            parent_value,
        )
        # Wait for AJAX spinner if present, plus baseline delay
        try:
            await page.locator("img[id*='loading'], .slds-spinner").wait_for(
                state="hidden", timeout=wait_for_ajax_ms
            )
        except Exception:
            await asyncio.sleep(wait_for_ajax_ms / 1000.0)

        child_loc = page.locator(child_locator)
        await child_loc.wait_for(state="visible", timeout=5000)
        await child_loc.evaluate(
            "(el, v) => { el.value = v; "
            "el.dispatchEvent(new Event('change', {bubbles: true})); }",
            child_value,
        )
        return True, f"parent={parent_value}, child={child_value}"
    except Exception as e:
        return False, f"cascading picklist failed: {e}"


# ── Run a single flow + variation ─────────────────────────────────────────


async def run_flow_variation(
    flow, variation, target_org: str, run_dir: Path,
    test_user: dict | None = None, headed: bool = False,
    sf_client=None, values: dict | None = None, runid: str | None = None,
) -> "FlowResult":
    """Execute only in the requested browser identity and report restoration."""
    from .diagnostics import exception_detail, redact
    result = FlowResult(
        flow_name=getattr(flow, "name", None) or flow.spec.name,
        profile=variation.profile, target_org=target_org, overall_status="INCOMPLETE",
    )
    if variation.profile != "admin" and not (test_user or {}).get("user_id"):
        result.error = "No test user configured for requested profile; no browser flow executed"
        result.side_effects["identity_status"] = "NOT_CHECKED"
        return result

    from playwright.async_api import async_playwright
    from .sf_client import SfClient
    from .flow_spec import FlowCtx
    from .cell_runner import run_cell
    from .library.lightning_page import LIGHTNING_SHELL_SELECTORS

    sf = sf_client or SfClient(target_org)
    try:
        admin_auth = auth.get_admin_auth(sf, target_org)
    except Exception as exc:
        result.overall_status = "FAIL"
        result.error = "Authentication failed: " + exception_detail(exc)
        return result

    async with async_playwright() as pw:
        sess = None
        login_attempted = False
        baseline_user = None
        identity = {"requested_profile": variation.profile, "status": "NOT_CHECKED"}
        try:
            sess = await auth.open_session(pw, admin_auth,
                                           cdp_endpoint=auth.cdp_endpoint_from_env(),
                                           headed=headed)
            page = sess.page
            await page.wait_for_selector(LIGHTNING_SHELL_SELECTORS, state="visible", timeout=30000)
            baseline_user = await auth.observe_user_id(page)
            identity.update(baseline_user_id=baseline_user, observed_user_id=baseline_user,
                            status="OBSERVED", basis="browser Aura CurrentUser.Id")
            if variation.profile != "admin":
                requested_user = test_user["user_id"]
                identity["requested_user_id"] = requested_user
                login_attempted = True
                await auth.login_as_user(page, admin_auth.instance_url,
                                         requested_user, org_id_18=admin_auth.org_id_18)
                observed_user = await auth.observe_user_id(page)
                identity["observed_user_id"] = observed_user
                if not auth.same_user(observed_user, requested_user):
                    identity["status"] = "MISMATCH"
                    raise auth.AuthError("Browser user does not match the requested test user; flow was not executed")
                identity["status"] = "MATCHED"
            else:
                identity["note"] = "admin is the configured role label; the observed User Id is not proof of administrative permissions"
            await _observe_org(page, identity, admin_auth.org_id_18, strict=bool(getattr(sess, "guarded", False)))

            ctx = FlowCtx(
                target_org=target_org, instance_url=admin_auth.instance_url,
                org_id_18=admin_auth.org_id_18, runid=runid or run_dir.name,
                run_dir=run_dir, variation=variation, profile=variation.profile,
                values=values or {}, seed=test_user or {}, handles={}, page=page, sf=sf,
            )
            result = await run_cell(flow, ctx)
        except auth.AuthError as exc:
            result.overall_status = "INCOMPLETE"
            result.error = exception_detail(exc)
        except Exception as exc:
            result.overall_status = "FAIL"
            result.error = exception_detail(exc)
        finally:
            result.side_effects = result.side_effects or {}
            result.side_effects["browser_identity"] = identity
            if sess is not None and login_attempted:
                try:
                    result.side_effects["session_restore"] = await auth.restore_original_user(
                        sess.page, admin_auth.instance_url, baseline_user)
                except Exception as exc:
                    result.overall_status = "FAIL"
                    result.side_effects["session_restore_error"] = exception_detail(exc)
                    result.side_effects["stop_suite"] = True
                    result.error = (result.error + "; " if result.error else "") + "Original browser session was not verified restored"
            await auth.close_session(sess)
    result.error = redact(result.error)
    result.side_effects = redact(result.side_effects)
    for step in result.steps:
        step.detail = redact(step.detail)
        step.side_effects = redact(step.side_effects)
    return result


async def _observe_org(page, identity: dict, expected_org_id: str, *, strict: bool) -> None:
    """Record the org the page is in, read from the page after login. A page in another
    org stops the run. An unreadable org stops a connected (guarded) run; elsewhere it is
    recorded as not checked, never as a match."""
    try:
        observed = await auth.observe_org_id(page)
    except auth.AuthError:
        identity["org_status"] = "NOT_CHECKED"
        if strict:
            raise auth.AuthError("The browser's org could not be read from the page; flow was not executed") from None
        return
    identity.update(observed_org_id_18=observed, org_basis="browser page (UserContext or oid)")
    if not (expected_org_id and observed[:15] == expected_org_id[:15]):
        identity["org_status"] = "MISMATCH"
        raise auth.AuthError("The browser's org does not match the selected org; flow was not executed")
    identity["org_status"] = "MATCHED"


def identity_report(result: "FlowResult") -> dict:
    """Who the browser was, observed in the page: the org, the admin before Login As, the
    user after it, and the admin after restoration. Never a session URL or token."""
    effects = result.side_effects or {}
    identity = effects.get("browser_identity") or {}
    restore = effects.get("session_restore") or {}
    before = identity.get("baseline_user_id")
    after = restore.get("user_id") if restore.get("status") == "OBSERVED" else None
    status = identity.get("status")
    org_status = identity.get("org_status")
    if status in ("MATCHED", "OBSERVED") and org_status != "MATCHED":
        status = "ORG_" + (org_status or "NOT_CHECKED")
    return {"org_id_18": identity.get("observed_org_id_18"), "admin_before": before,
            "user_after_login_as": identity.get("observed_user_id") if identity.get("requested_user_id") else None,
            "admin_restored": after, "restored": bool(before and after and before[:15] == after[:15]),
            "status": status}
