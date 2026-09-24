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

from dataclasses import dataclass, field
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
    return Guard(org_alias=target_org, org_id_18=info.org_id_18, host_key=host_key)


async def install(context, guard: Guard) -> None:
    """Check every request of a Playwright browser context against the guard."""
    async def handle(route, request):
        if request_allowed(guard, request.url, request.method):
            await route.continue_()
        else:
            guard.refused.append(f"{request.method} {request.url.split('?')[0]}")
            await route.abort("blockedbyclient")
    await context.route("**/*", handle)
