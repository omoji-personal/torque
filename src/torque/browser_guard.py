"""Connected mode's guard for Torque's own browser (`torque browser ... --target-org ORG`).

Browser tools driven by the AI session cannot show which org a page is in after
navigation and redirects, so connected mode refuses their changes. Torque's own
Playwright session is the one browser path that may change an org: before it
starts, the org is resolved live and must match the client's consent and a
granted browser window; while it runs, every request the browser makes (every
navigation, redirect, frame and background call) is checked against that org's
My Domain host and refused when it goes to another Salesforce org, or when it
would change anything on a Salesforce host that is not the approved org."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import time
import urllib.parse

from . import approval, consent, workspace as ws
from .connected_routes import SF_HOSTS
from .gate_connected import _same_org, org_key

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


class GuardRefused(Exception):
    """Torque's browser may not start (or continue) against this org in connected mode."""


@dataclass
class Guard:
    org_alias: str
    org_id_18: str
    host_key: tuple[str, str]
    refused: list[str] = field(default_factory=list)
    # The window's end, and a check that the window and the client's consent still hold.
    namespaces: tuple[str, ...] = ()
    expires_at: float | None = None
    recheck: object = None
    recheck_every: float = 1.0
    stopped: bool = False
    _last_check: float = 0.0
    _last_ok: bool = True

    def authorized(self, now: float | None = None, write: bool = True) -> bool:
        """The session may still act: not stopped, inside its window, and its window and
        consent still hold. A write-capable request always checks the window and consent
        again, with no cache; a read uses a check at most recheck_every seconds old."""
        now = time.time() if now is None else now
        if self.stopped or (self.expires_at is not None and now > self.expires_at):
            return False
        if self.recheck is not None and (write or now - self._last_check >= self.recheck_every):
            try:
                self._last_ok = bool(self.recheck())
            except Exception:
                self._last_ok = False
            self._last_check = now
        return self._last_ok


# Salesforce domains no request may reach except the approved org's own hosts (and a
# few shared, non-org hosts). Used as Chromium host-resolver rules, so a redirect hop,
# a service worker or any other request to another org cannot even resolve its name.
BLOCKED_DOMAINS = ("salesforce.com", "force.com", "salesforce-setup.com", "site.com", "visualforce.com",
                   "cloudforce.com", "database.com", "salesforce-sites.com", "documentforce.com",
                   "salesforce-experience.com", "lightning.com", "sfdc.net")
SHARED_HOSTS = ("static.lightning.force.com", "login.salesforce.com", "test.salesforce.com")
# The exact host names an org uses (no wildcards: a wildcard after the My Domain name
# would also match a sandbox's or another org's hosts). {ns} is a Visualforce namespace:
# "c" for the org's own pages, plus the managed packages the workspace lists.
ORG_HOST_FORMS = ("{p}{k}.my.salesforce.com", "{p}{k}.lightning.force.com", "{p}{k}.my.salesforce-setup.com",
                  "{p}{k}.my.site.com", "{p}{k}.file.force.com", "{p}{k}.my.salesforce-sites.com")
NAMESPACED_FORMS = ("{p}--{ns}{k}.vf.force.com", "{p}--{ns}{k}.file.force.com", "{p}--{ns}{k}.documentforce.com")


def org_hosts(guard: Guard) -> list[str]:
    prefix, kind = guard.host_key
    k = "" if kind == "prod" else f".{kind}"
    namespaces = ["c", *[n for n in guard.namespaces if n != "c"]]
    return ([form.format(p=prefix, k=k) for form in ORG_HOST_FORMS]
            + [form.format(p=prefix, k=k, ns=ns.casefold()) for form in NAMESPACED_FORMS for ns in namespaces])


def resolver_rules(guard: Guard, approved_target: str | None = None) -> str:
    """Chromium --host-resolver-rules: every Salesforce domain unresolvable except the
    approved org's hosts. approved_target maps those hosts somewhere (tests only)."""
    approved = [f"MAP {h} {approved_target}" if approved_target else f"EXCLUDE {h}" for h in org_hosts(guard)]
    shared = [f"EXCLUDE {h}" for h in SHARED_HOSTS]
    blocked = [rule for domain in BLOCKED_DOMAINS for rule in (f"MAP *.{domain} ~NOTFOUND", f"MAP {domain} ~NOTFOUND")]
    return ", ".join(approved + shared + blocked)


def launch_options(guard: Guard, approved_target: str | None = None) -> tuple[dict, dict]:
    """(browser launch options, browser context options) for a guarded session: the
    resolver rules, no proxy (a proxy would resolve names itself), no service workers."""
    return ({"args": [f"--host-resolver-rules={resolver_rules(guard, approved_target)}", "--no-proxy-server"]},
            {"service_workers": "block"})


def request_allowed(guard: Guard, url: str, method: str) -> bool:
    """A request to the approved org, or one that changes nothing outside Salesforce."""
    host = (urllib.parse.urlsplit(url).hostname or "").casefold()
    key = org_key(url)
    if key is not None:
        return _same_org(key, guard.host_key)
    if SF_HOSTS.search(host) and (method or "").upper() not in SAFE_METHODS:
        return False
    return True


def connected_guard(target_org: str, resolve=None) -> Guard | None:
    """The guard for this run, or None when the workspace is not in connected mode.
    Raises GuardRefused when connected mode cannot be ruled out but the run is not
    allowed: no client, unusable consent, an org outside it, another org ID behind
    the alias, no granted browser window, or no My Domain address to check against."""
    from jsc_revert.wrappers import _common
    try:
        scope = _common._connected_scope()
    except _common.IndeterminateScope as exc:
        raise GuardRefused(f"connected mode cannot be ruled out ({exc})") from exc
    if scope is None:
        return None
    workspace, client = scope
    if not client:
        raise GuardRefused("connected mode: run Torque's browser with --workspace PATH --client NAME")
    try:
        item = approval._usable_consent(workspace, client)
    except ws.WorkspaceError as exc:
        raise GuardRefused(str(exc)) from exc
    entry = consent.approved_org(item, target_org)
    if entry is None:
        raise GuardRefused(f"org {target_org!r} is not in this client's consent")
    if resolve is None:
        from jsc_revert.org_detect import resolve_org as resolve
    info = resolve(target_org)
    if info is None:
        raise GuardRefused(f"could not resolve {target_org!r} live")
    if info.org_id_18 != entry["org_id_18"]:
        raise GuardRefused(f"the org ID for {target_org!r} is now {info.org_id_18}; the consent records "
                           f"{entry['org_id_18']}")
    config = ws.load_workspace(workspace)[1]
    window = approval.find_browser_approval(workspace, client, target_org, config=config)
    if window is None or window.get("org_id_18") != info.org_id_18:
        raise GuardRefused(f"no granted browser window for {target_org}; ask with torque approval request --browser")
    host_key = org_key(entry.get("instance_url") or "") or org_key(getattr(info, "instance_url", "") or "")
    if host_key is None:
        raise GuardRefused(f"the My Domain address of {target_org} is not known; record the consent again")

    def recheck() -> bool:
        """The same window is still valid and the consent still usable for this org."""
        current = approval.find_browser_approval(workspace, client, target_org, config=config)
        item_now = consent.load_consent(workspace, client)
        entry_now = consent.approved_org(item_now, target_org)
        return bool(current and current.get("id") == window.get("id")
                    and not consent.consent_problems(item_now, client=ws.slug_for(client))
                    and entry_now and entry_now.get("org_id_18") == info.org_id_18)
    from .namespaces import DEFAULT_MANAGED
    extra = config.get("managed_namespaces") if isinstance(config.get("managed_namespaces"), list) else []
    return Guard(org_alias=target_org, org_id_18=info.org_id_18, host_key=host_key,
                 namespaces=tuple(n for n in (*DEFAULT_MANAGED, *extra) if isinstance(n, str) and n.isalnum()),
                 expires_at=approval._epoch(window["expires_at"]), recheck=recheck)


async def _stop(context, guard: Guard) -> None:
    """End the session: close every page and the browser context."""
    guard.stopped = True
    for page in list(getattr(context, "pages", []) or []):
        try:
            await page.close()
        except Exception:
            pass
    try:
        await context.close()
    except Exception:
        pass


async def install(context, guard: Guard) -> None:
    """Check every request of a Playwright browser context against the guard, and stop
    the session (close its pages and context, refuse every further request) when the
    window ends or the consent or window no longer holds."""
    async def handle(route, request):
        if not guard.authorized(write=(request.method or "").upper() not in SAFE_METHODS):
            guard.refused.append(f"{request.method} {request.url.split('?')[0]} (session stopped)")
            already = guard.stopped
            guard.stopped = True
            await route.abort("blockedbyclient")
            if not already:
                asyncio.ensure_future(_stop(context, guard))
            return
        if request_allowed(guard, request.url, request.method):
            await route.continue_()
        else:
            guard.refused.append(f"{request.method} {request.url.split('?')[0]}")
            await route.abort("blockedbyclient")
    await context.route("**/*", handle)

    async def watch():
        while not guard.stopped:
            remaining = (guard.expires_at - time.time()) if guard.expires_at is not None else 5.0
            await asyncio.sleep(max(0.0, min(5.0, remaining + 0.01)))
            if not guard.authorized():
                await _stop(context, guard)
                return
    guard._watch = asyncio.ensure_future(watch())
