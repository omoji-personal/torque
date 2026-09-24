"""Connected mode's guard for Torque's own browser (`torque browser ... --target-org ORG`).

Browser tools driven by the AI session cannot show which org a page is in after
navigation and redirects, so connected mode refuses their changes. Torque's own
Playwright session is the one browser path that may change an org: before it
starts, the org is resolved live and must match the client's consent and a
granted browser window; while it runs, every request the route handler sees (each navigation, frame and
background call) rereads the window and consent and must go to the approved org's
exact hosts (or read a static host), and host-resolver rules keep every other
host, redirect hops included, from resolving."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import time
import urllib.parse

from . import approval, consent, workspace as ws
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
    recheck_every: float = 0.0  # unused; kept so older callers still construct a Guard
    stopped: bool = False

    def authorized(self, now: float | None = None, write: bool = True) -> bool:
        """The session may still act: not stopped, inside its window, and its window and
        consent still hold, read again for every request (no cache, reads included)."""
        now = time.time() if now is None else now
        if self.stopped or (self.expires_at is not None and now > self.expires_at):
            return False
        if self.recheck is None:
            return True
        try:
            return bool(self.recheck())
        except Exception:
            return False


# The static, read-only content host the Lightning UI loads. It holds no org data, so a
# redirected request that reaches it (the route handler never sees a redirect hop) cannot
# write to any org.
STATIC_HOSTS = ("static.lightning.force.com",)
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
    """Chromium --host-resolver-rules: every host unresolvable (Salesforce or not, IP
    literals, localhost and trailing-dot forms included) except the approved org's exact
    hosts and the static hosts. approved_target maps those hosts somewhere (tests only)."""
    allowed = [*org_hosts(guard), *STATIC_HOSTS]
    rules = [f"MAP {h} {approved_target}" if approved_target else f"EXCLUDE {h}" for h in allowed]
    return ", ".join([*rules, "MAP * ~NOTFOUND"])


def launch_options(guard: Guard, approved_target: str | None = None) -> tuple[dict, dict]:
    """(browser launch options, browser context options) for a guarded session: the
    resolver rules, no proxy (a proxy would resolve names itself), no service workers."""
    return ({"args": [f"--host-resolver-rules={resolver_rules(guard, approved_target)}", "--no-proxy-server"]},
            {"service_workers": "block"})


NETWORK_SCHEMES = ("http", "https", "ws", "wss")


def request_allowed(guard: Guard, url: str, method: str) -> bool:
    """The resolver's host list, for each request the handler sees: the approved org's
    exact hosts for any method, the static hosts for GET, HEAD and OPTIONS only, and no
    other host. A URL with no network destination (data:, blob:) is allowed."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.casefold() not in NETWORK_SCHEMES:
        return True
    host = (parts.hostname or "").casefold()
    if host in org_hosts(guard):
        return True
    return host in STATIC_HOSTS and (method or "").upper() in SAFE_METHODS


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
    window ends or the consent or window no longer holds.

    Allowed requests go on unchanged (route.continue_): the browser sends them and
    follows any redirect itself, so no header or body is ever replayed. Playwright does
    not call this handler for a redirect hop; the resolver rules (launch_options) are
    what stop a hop to any host but the approved org's and the static hosts."""
    async def refuse(route, method, url, why=""):
        guard.refused.append(f"{method} {url.split('?')[0]}{why}")
        await route.abort("blockedbyclient")

    async def handle(route, request):
        method = (request.method or "GET").upper()
        if not guard.authorized():
            already = guard.stopped
            guard.stopped = True
            await refuse(route, method, request.url, " (session stopped)")
            if not already:
                asyncio.ensure_future(_stop(context, guard))
            return
        if not request_allowed(guard, request.url, method):
            await refuse(route, method, request.url)
            return
        await route.continue_()
    await context.route("**/*", handle)

    async def watch():
        while not guard.stopped:
            remaining = (guard.expires_at - time.time()) if guard.expires_at is not None else 5.0
            await asyncio.sleep(max(0.0, min(5.0, remaining + 0.01)))
            if not guard.authorized():
                await _stop(context, guard)
                return
    guard._watch = asyncio.ensure_future(watch())
