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
    username: str = ""  # the username sf resolves for the alias
    api_version: str = ""


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
        username=d2.get("username", "") or "",
        api_version=d2.get("apiVersion", "") or "",
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
    guarded: bool = False  # connected mode: the page's username must match the alias's


async def open_session(pw, admin_auth, *, cdp_endpoint: str | None = None,
                       headed: bool = False, timeout: int = 30000) -> BrowserSession:
    """Return a BrowserSession with an authenticated page, via CDP or frontdoor.

    In a connected Torque workspace, the org is checked against the client's consent
    and a granted browser window first, and every request of the browser context is
    checked against that org before it is sent (torque.browser_guard)."""
    browser = context = None
    guard = None
    try:
        from torque.browser_guard import GuardRefused, connected_guard
    except ImportError:
        connected_guard = None
    if connected_guard is not None:
        try:
            guard = connected_guard(admin_auth.target_org)
        except GuardRefused as exc:
            raise AuthError(f"connected mode: {exc}") from None
    if guard is not None and debug_env_problem():
        raise AuthError("connected mode: " + debug_env_problem())
    if guard is not None and getattr(guard, "delegated", True) and headed:
        raise AuthError("connected mode: a browser window granted by a delegated approver runs headless "
                        "only; run without --headed")
    if guard is not None and cdp_endpoint:
        raise AuthError("connected mode: an attached (CDP) browser cannot be guarded; Torque launches its own "
                        "browser, whose requests it checks before they are sent")
    if cdp_endpoint:
        try:
            browser = await pw.chromium.connect_over_cdp(cdp_endpoint)
            if not browser.contexts:
                raise AuthError("Configured CDP browser has no context")
            context = browser.contexts[0]
            if guard is not None:
                from torque.browser_guard import install
                await install(context, guard)
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
        launch_options, context_options = {}, {}
        if guard is not None:
            from torque.browser_guard import launch_options as guarded_options
            launch_options, context_options = guarded_options(guard)
        browser = await pw.chromium.launch(headless=not headed, **launch_options)
        context = await browser.new_context(**context_options)
        if guard is not None:
            from torque.browser_guard import install
            await install(context, guard)
        page = await context.new_page()
        await page.goto(admin_auth.frontdoor_url, wait_until="domcontentloaded", timeout=timeout)
        title = await page.title()
        if "login" in (title or "").lower():
            raise AuthError("Frontdoor did not establish a session")
        return BrowserSession(page=page, channel="frontdoor", browser=browser,
                              context=context, owns_browser=True, guarded=guard is not None)
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


def debug_env_problem(env=None) -> str | None:
    """Playwright settings that would print the navigated frontdoor URL (DEBUG, DEBUG_FILE)
    or force a visible browser (PWDEBUG), else None. Any non-empty DEBUG counts: Node's
    debug module turns Playwright's pw:* logs on for *, pw*, p* and other patterns."""
    import os
    env = os.environ if env is None else env
    found = [name for name in ("DEBUG", "PWDEBUG", "DEBUG_FILE") if env.get(name)]
    if not found:
        return None
    return (f"{', '.join(found)} is set; Playwright debugging prints the session URL or opens a visible "
            "browser, so a connected run does not start with it. Unset it and run again")


def in_connected_mode() -> bool:
    """This run is in a connected Torque workspace (or that cannot be ruled out)."""
    try:
        from jsc_revert.wrappers import _common
    except ImportError:
        return False
    try:
        return _common._connected_scope() is not None
    except _common.IndeterminateScope:
        return True


def refuse_debug_env_when_connected() -> None:
    """Call before Playwright starts: its driver reads these variables when it starts."""
    problem = debug_env_problem()
    if problem and in_connected_mode():
        raise AuthError("connected mode: " + problem)


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


UI_API_VERSION = "62.0"  # fallback when sf org display names no apiVersion
_USER_ID = r"005[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?"
_USERNAME = r"[^\s@]{1,80}@[^\s@]{1,80}"
# Runs in the page: a same-origin UI API read of the page user's own Username (the
# Lightning domain serves it on the session; the REST root does not). Only the HTTP
# status and the Username string come back; the response body stays in the page.
_PAGE_USERNAME_JS = r"""async ([version, userId]) => {
    try {
        const response = await fetch('/services/data/v' + version + '/ui-api/records/' + userId
                                     + '?fields=User.Username',
                                     {credentials: 'same-origin', headers: {Accept: 'application/json'}});
        if (!response.ok) return {status: response.status, username: null};
        const body = await response.json();
        const field = body && body.apiName === 'User' && body.fields && body.fields.Username;
        return {status: response.status, username: field && typeof field.value === 'string' ? field.value : null};
    } catch (_) { return {status: 0, username: null}; }
}"""


async def observe_username(page, user_id: str, api_version: str | None = None) -> str:
    """Read the Username of the page's user (its Aura User Id) from inside the page.

    A Salesforce username is globally unique, so matching it with the username sf
    resolves for the org alias proves both the org and the user. Unknown is an error,
    never a match; only the Username is kept, never the response."""
    version = api_version if isinstance(api_version, str) and re.fullmatch(r"\d{2,3}\.0", api_version) \
        else UI_API_VERSION
    if not isinstance(user_id, str) or not re.fullmatch(_USER_ID, user_id):
        raise AuthError("Browser username cannot be read without a valid User Id")
    try:
        found = await page.evaluate(_PAGE_USERNAME_JS, [version, user_id])
    except Exception as exc:
        raise AuthError(f"Browser username could not be read ({type(exc).__name__})") from None
    status = found.get("status") if isinstance(found, dict) else None
    username = found.get("username") if isinstance(found, dict) else None
    if status != 200:
        code = status if isinstance(status, int) else "no response"
        raise AuthError(f"Browser username could not be read (UI API HTTP {code})")
    if not isinstance(username, str) or not re.fullmatch(_USERNAME, username):
        raise AuthError("Browser username is unavailable in the UI API response")
    return username


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
