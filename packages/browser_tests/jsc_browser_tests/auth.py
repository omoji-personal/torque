"""auth.py — Salesforce authentication for browser tests.

Per design-v4 Closure 3: sf CLI frontdoor URL for admin context + Login As for
non-admin profiles. NEVER stores passwords/tokens in test-user seed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple
from .diagnostics import exception_detail


class AuthError(Exception):
    """Raised when sf CLI auth/frontdoor URL retrieval fails."""


class AdminAuth(NamedTuple):
    target_org: str
    org_id_18: str
    instance_url: str
    frontdoor_url: str  # CONTAINS access token; do NOT log or commit


def get_admin_auth(sf_client, target_org: str) -> AdminAuth:
    """Authenticate as admin via the injected SfClient; return frontdoor URL.

    The frontdoor URL contains an access token — never log/commit it.
    """
    frontdoor = sf_client.org_frontdoor_url(target_org)
    if not frontdoor:
        raise AuthError(f"sf org open returned no url for {target_org}")
    d2 = sf_client.org_display(target_org)
    return AdminAuth(
        target_org=target_org,
        org_id_18=d2.get("id", ""),
        instance_url=d2.get("instanceUrl", ""),
        frontdoor_url=frontdoor,
    )


@dataclass
class BrowserSession:
    """A page ready to drive, plus how to tear it down.

    Two channels, chosen by whether a CDP endpoint is configured:

    * **frontdoor** (default) — launch our own browser and establish the session from the sf CLI
      access token. Works only where the org does NOT enforce passkey/MFA; where it does, the
      token cannot carry the second factor and Salesforce serves the login form instead.
    * **cdp** — attach to a browser the OPERATOR already launched and authenticated. Required for
      passkey orgs: automation-launched Chrome disables extensions, so a password manager holding
      the passkey is unreachable by construction. See docs/browser-testing-authenticated-setup.md.
    """
    page: object
    channel: str          # "frontdoor" | "cdp"
    browser: object
    context: object
    owns_browser: bool    # False when attached — never close the operator's browser


async def open_session(pw, admin_auth, *, cdp_endpoint: str | None = None,
                       headed: bool = False, timeout: int = 30000) -> BrowserSession:
    """Return a BrowserSession with an authenticated page, via CDP or frontdoor."""
    browser = context = None
    if cdp_endpoint:
        try:
            browser = await pw.chromium.connect_over_cdp(cdp_endpoint)
            if not browser.contexts:
                raise AuthError("Configured CDP browser has no context")
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(admin_auth.instance_url, wait_until="domcontentloaded", timeout=timeout)
            title = await page.title()
            if "login" in (title or "").lower():
                raise AuthError("Configured CDP browser is not logged into the selected org")
            return BrowserSession(page=page, channel="cdp", browser=browser,
                                  context=context, owns_browser=False)
        except Exception as exc:
            # Connection URLs can also carry credentials. Never echo an attachment URL.
            raise AuthError(f"CDP session setup failed ({type(exc).__name__}); check the selected browser's authentication") from None

    try:
        browser = await pw.chromium.launch(headless=not headed)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(admin_auth.frontdoor_url, wait_until="domcontentloaded", timeout=timeout)
        title = await page.title()
        if "login" in (title or "").lower():
            raise AuthError("Frontdoor did not establish a session")
        return BrowserSession(page=page, channel="frontdoor", browser=browser,
                              context=context, owns_browser=True)
    except Exception as exc:
        # Playwright includes the full navigated frontdoor URL in many exceptions.
        # Close only our resources and retain the exception type, never its URL/message.
        if context is not None:
            try: await context.close()
            except Exception: pass
        if browser is not None:
            try: await browser.close()
            except Exception: pass
        raise AuthError(f"Frontdoor session setup failed ({type(exc).__name__}); check authentication or use an authenticated TORQUE_BROWSER_CDP session") from None


async def close_session(sess) -> None:
    """Tear down only what we created. Never closes an attached operator browser."""
    if sess is None:
        return
    if sess.owns_browser:
        try: await sess.context.close()
        except Exception: pass
        try: await sess.browser.close()
        except Exception: pass


def cdp_endpoint_from_env() -> str | None:
    """JSC_BROWSER_CDP, e.g. http://127.0.0.1:9222. Unset -> frontdoor channel."""
    import os
    v = (os.environ.get("TORQUE_BROWSER_CDP") or os.environ.get("JSC_BROWSER_CDP") or "").strip()
    return v or None


async def observe_user_id(page) -> str:
    """Read the active page's user, never the independently authenticated CLI user.

    Salesforce documents this Aura value provider in its Embedded Messaging setup
    examples. It is not available in every UI; absence is unknown identity, never
    proof that Login As succeeded. Live browser acceptance is still required.
    """
    try:
        await page.wait_for_function("""() => {
            try { return Boolean(window.$A && window.$A.get('$SObjectType.CurrentUser.Id')); }
            catch (_) { return false; }
        }""", timeout=10000)
        value = await page.evaluate("""() => {
            try { return window.$A && window.$A.get('$SObjectType.CurrentUser.Id'); }
            catch (_) { return null; }
        }""")
    except Exception as exc:
        raise AuthError(f"Current browser user could not be observed ({type(exc).__name__})") from None
    if not isinstance(value, str) or not re.fullmatch(r"005[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?", value):
        raise AuthError("Current browser User Id is unavailable; named-user coverage is unverified")
    return value


def same_user(observed: str, expected: str) -> bool:
    return bool(observed and expected and observed[:15] == expected[:15])


async def login_as_user(page, instance_url: str, target_user_id: str,
                        org_id_18: str | None = None) -> None:
    """Switch the active session from admin to a target user (Login As / "su").

    Primary path is the direct Login-As servlet
    (`/servlet/servlet.su?oid=<org>&suorgadminid=<user15>`), which is far more
    reliable than the Setup UI "Login" link — that link is gated by the
    "Administrators Can Log in as Any User" preference, renders inside a classic
    iframe, and/or sits in Lightning shadow DOM, so role/text locators miss it
    (empirically not found on sample-sandbox 2026-06-02). The servlet drops into
    Classic; we then navigate to a Lightning URL so the impersonated session runs
    in LEX like a real end user.

    Args:
        page: Playwright Page (already authenticated as admin)
        instance_url: org's instance URL (https://x.my.salesforce.com)
        target_user_id: 15- or 18-char User Id (per test-users.json seed)
        org_id_18: org id (enables the servlet path; required for reliable Login As)
    """
    if not isinstance(target_user_id, str) or not re.fullmatch(r"005[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?", target_user_id):
        raise AuthError("Login As requires a valid 15- or 18-character User Id")
    if org_id_18:
        uid15 = target_user_id[:15]
        su = (f"{instance_url}/servlet/servlet.su?oid={org_id_18}"
              f"&suorgadminid={uid15}&retURL=%2F&targetURL=%2F")
        await page.goto(su, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2500)  # impersonation handshake
        await page.goto(f"{instance_url}/lightning/page/home",
                        wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_load_state("domcontentloaded", timeout=30000)
        return

    # Legacy fallback: Setup UI "Login" link (only if org id wasn't provided).
    user_detail_url = f"{instance_url}/lightning/setup/ManageUsers/page?address=%2F{target_user_id}"
    await page.goto(user_detail_url, wait_until="domcontentloaded", timeout=30000)
    try:
        await page.get_by_role("link", name="Login").click(timeout=10000)
    except Exception:
        try:
            classic_frame = page.frame_locator("iframe[title*='Setup'], iframe.setupcomp")
            await classic_frame.get_by_role("link", name="Login").click(timeout=10000)
        except Exception as e:
            raise AuthError(
                f"Could not Login As user {target_user_id} (no org_id_18 for the "
                f"servlet path, and the UI Login link was not found): {exception_detail(e)}") from None
    await page.wait_for_load_state("domcontentloaded", timeout=30000)


async def logout_as_user(page, instance_url: str) -> None:
    """Return to admin context via Logout As link."""
    # Navigate home; Logout As link is typically in the global header
    await page.goto(f"{instance_url}/lightning/page/home",
                    wait_until="domcontentloaded", timeout=30000)
    try:
        await page.get_by_role("link", name="Log out as").click(timeout=10000)
    except Exception:
        # Try alternative selector
        try:
            await page.locator("a[href*='servlet.sulogout']").click(timeout=10000)
        except Exception:
            raise AuthError("Could not return from Login As; stop this browser run and restore the admin session.")
    await page.wait_for_load_state("domcontentloaded", timeout=15000)


async def restore_original_user(page, instance_url: str, baseline_user: str) -> dict:
    """Observe actual session state before deciding whether Logout As is needed.

    A failed Login As can leave the original user active. Conversely, an action
    timeout does not establish which user is now active. Only a fresh observation
    of the original User Id proves restoration; unknown identity remains failure.
    """
    initial_error = None
    try:
        current = await observe_user_id(page)
    except Exception as exc:
        current = None
        initial_error = exception_detail(exc)
    if same_user(current, baseline_user):
        return {"status": "OBSERVED", "user_id": current, "action": "ALREADY_BASELINE"}

    action_error = None
    try:
        await logout_as_user(page, instance_url)
    except Exception as exc:
        action_error = exception_detail(exc)
    try:
        restored = await observe_user_id(page)
    except Exception as exc:
        detail = exception_detail(exc)
        if action_error:
            detail = action_error + "; " + detail
        raise AuthError("Original browser user could not be observed after Logout As: " + detail) from None
    if not same_user(restored, baseline_user):
        raise AuthError("Logout As did not restore the original browser user")
    evidence = {"status": "OBSERVED", "user_id": restored, "action": "LOGOUT_AS"}
    if initial_error:
        evidence["initial_observation_error"] = initial_error
    if action_error:
        # Keep the failed action evidence even when a later observation proves
        # that the identity actually returned to baseline.
        evidence["action_warning"] = action_error
    return evidence


# ── Forbidden patterns for replay-script sanitizer (per design-v4 Closure 4) ──
import re

FORBIDDEN_REPLAY_PATTERNS = [
    re.compile(r"frontdoor\.jsp\?sid="),
    re.compile(r"\bsid=[\w!.\-]+", re.IGNORECASE),
    re.compile(r"access_?token", re.IGNORECASE),
    re.compile(r"Bearer\s+[\w.\-]+"),
    re.compile(r"Authorization:\s*\w+"),
    re.compile(r"Cookie:\s*[\w=;]+"),
    re.compile(r"oauth_token"),
    re.compile(r"refresh_?token", re.IGNORECASE),
]


def scan_replay_script(content: str) -> list[str]:
    """Scan replay-script content for forbidden credential patterns.

    Returns list of human-readable violation descriptions (empty if clean).
    """
    violations = []
    for pattern in FORBIDDEN_REPLAY_PATTERNS:
        matches = pattern.findall(content)
        if matches:
            
            violations.append(
                f"forbidden pattern {pattern.pattern!r} found "
                f"({len(matches)} occurrence(s); matched values redacted)"
            )
    return violations
