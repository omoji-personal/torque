"""R2h: no manual redirect loop. The browser handles every request natively; the
host-resolver rules enforce every hop, and the route handler checks each request it
sees (destination, method, window and consent). Written failing first."""
import asyncio
from fnmatch import fnmatchcase
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from torque import browser_guard as bg

SBX_KEY = ("acme--sbx", "sandbox")
PROD_KEY = ("acme", "prod")
APPROVED = "https://acme--sbx.sandbox.lightning.force.com"


def guard(key=SBX_KEY, recheck=lambda: True, expires=600):
    return bg.Guard(org_alias="acme", org_id_18="00D000000000001AAA", host_key=key,
                    expires_at=time.time() + expires, recheck=recheck)


def resolves(rules: str, host: str) -> bool:
    """How Chromium reads the rules: an EXCLUDE pattern keeps a host out of every MAP
    rule; otherwise the first matching MAP rule applies."""
    parts = [r.strip().split(" ") for r in rules.split(",")]
    if any(p[0] == "EXCLUDE" and fnmatchcase(host, p[1]) for p in parts):
        return True
    return not any(p[0] == "MAP" and fnmatchcase(host, p[1]) and p[2] == "~NOTFOUND" for p in parts)


BLOCKED = ["login.salesforce.com", "test.salesforce.com", "acme.my.salesforce.com",
           "beta.my.salesforce-sites.com", "beta.salesforce-experience.com", "beta.my.site.com",
           "other--sbx.sandbox.lightning.force.com", "acme--sbx--c.sandbox.vf.force.com.evil.force.com"]


@pytest.mark.parametrize("host", BLOCKED)
def test_resolver_blocks_login_hosts_other_orgs_and_codex_cases(host):
    rules = bg.resolver_rules(guard())
    assert not resolves(rules, host)


def test_resolver_has_no_login_host_exception():
    for key in (SBX_KEY, PROD_KEY):
        rules = bg.resolver_rules(guard(key))
        assert "login.salesforce.com" not in rules and "test.salesforce.com" not in rules


@pytest.mark.parametrize("host", ["acme--sbx.sandbox.my.salesforce.com", "acme--sbx.sandbox.lightning.force.com",
                                  "acme--sbx--c.sandbox.vf.force.com", "static.lightning.force.com"])
def test_resolver_keeps_the_org_and_static_hosts(host):
    assert resolves(bg.resolver_rules(guard()), host)


def test_static_hosts_are_listed_exactly():
    assert bg.STATIC_HOSTS == ("static.lightning.force.com",)
    assert all("*" not in h for h in bg.STATIC_HOSTS)


class Route:
    """A route that records continue_ and fails on any replay (fetch or fulfill)."""

    def __init__(self):
        self.result = None
        self.continue_kwargs = None

    async def continue_(self, **kwargs):
        self.result = "continue"
        self.continue_kwargs = kwargs

    async def fallback(self, **kwargs):
        self.result = "continue"
        self.continue_kwargs = kwargs

    async def abort(self, reason=None):
        self.result = "abort"

    async def fetch(self, **kw):
        raise AssertionError("the guard must never replay a request")

    async def fulfill(self, **kw):
        raise AssertionError("the guard must never replay a request")


class Context:
    handler = None
    closed = False

    async def route(self, pattern, handler):
        self.handler = handler

    async def close(self):
        self.closed = True


def request(url, method="POST"):
    return SimpleNamespace(url=url, method=method, headers={"authorization": "Bearer secret"}, post_data="secret-body",
                           is_navigation_request=lambda: False)


def run(g, *reqs, between=None):
    async def main():
        context = Context()
        await bg.install(context, g)
        results = []
        for i, req in enumerate(reqs):
            if between and i:
                between()
            route = Route()
            await context.handler(route, req)
            results.append((route.result, route.continue_kwargs))
        await asyncio.sleep(0)
        return results, context.closed
    return asyncio.run(main())


@pytest.mark.parametrize("url", ["https://static.lightning.force.com/x", "https://login.salesforce.com/",
                                 "https://test.salesforce.com/", "https://beta.salesforce-experience.com/s",
                                 "https://beta.my.salesforce-sites.com/p", "https://acme.my.salesforce.com/aura"])
def test_a_post_outside_the_org_is_refused_on_the_initial_request(url):
    (result,), _ = run(guard(), request(url))
    assert result[0] == "abort"


@pytest.mark.parametrize("url", ["https://beta.my.salesforce-sites.com/p", "https://beta.salesforce-experience.com/s",
                                 "https://login.salesforce.com/", "https://acme.my.salesforce.com/"])
def test_a_read_of_another_salesforce_host_is_refused(url):
    (result,), _ = run(guard(), request(url, "GET"))
    assert result[0] == "abort"


def test_a_static_read_is_allowed():
    (result,), _ = run(guard(), request("https://static.lightning.force.com/app.js", "GET"))
    assert result[0] == "continue"


def test_no_replay_the_browser_sends_the_request_unchanged():
    (result,), _ = run(guard(), request(APPROVED + "/aura"))
    assert result == ("continue", {})


def test_suspension_closes_the_context_on_the_next_request():
    state = {"ok": True}
    results, closed = run(guard(recheck=lambda: state["ok"]), request(APPROVED + "/aura", "GET"),
                          request(APPROVED + "/aura", "GET"), between=lambda: state.update(ok=False))
    assert [r[0] for r in results] == ["continue", "abort"] and closed


def test_expiry_closes_the_context_on_the_next_request():
    g = guard()
    results, closed = run(g, request(APPROVED + "/a", "GET"), request(APPROVED + "/b", "GET"),
                          between=lambda: setattr(g, "expires_at", time.time() - 1))
    assert [r[0] for r in results] == ["continue", "abort"] and closed


def test_every_request_rechecks():
    calls = []
    run(guard(recheck=lambda: calls.append(1) or True), *[request(APPROVED + "/a", "GET")] * 3)
    assert len(calls) == 3


def _chromium():
    from pathlib import Path
    import os
    pw = pytest.importorskip("playwright.async_api")
    cache = Path.home() / "Library" / "Caches" / "ms-playwright"
    for root in (Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")), cache):
        found = sorted(root.glob("chromium_headless_shell-*/*/chrome-headless-shell")) if root.is_dir() else []
        if found:
            return pw, str(found[-1])
    pytest.skip("no Chromium build available")


TARGETS = ["login.salesforce.com", "test.salesforce.com", "acme.my.salesforce.com",
           "beta.my.salesforce-sites.com", "beta.salesforce-experience.com"]


def test_real_browser_post_redirects_fail_to_resolve():
    pw, exe = _chromium()
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _serve(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            hits.append((self.command, self.headers.get("Host", "").split(":")[0], self.path, body))
            if self.path.startswith("/r"):
                code, target = self.path[2:].split("/", 1)
                self.send_response(int(code))
                self.send_header("Location", f"http://{target}:{port}/arrived")
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                page = b"<html>ok</html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
        do_GET = _serve
        do_POST = _serve

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    live = guard()
    launch, context_options = bg.launch_options(live, approved_target="127.0.0.1")
    base = f"http://acme--sbx.sandbox.my.salesforce.com:{port}"

    async def main():
        failed = []
        async with pw.async_playwright() as p:
            browser = await p.chromium.launch(executable_path=exe, **launch)
            context = await browser.new_context(**context_options)
            context.on("requestfailed", lambda r: failed.append((r.url, r.failure)))
            await bg.install(context, live)
            page = await context.new_page()
            await page.goto(base + "/start")
            statuses = []
            for code in (307, 308):
                for target in TARGETS:
                    statuses.append(await page.evaluate(
                        "u => fetch(u, {method: 'POST', body: 'secret-body', headers: {Authorization: 'Bearer s'}})"
                        ".then(r => r.status).catch(() => 'blocked')", f"/r{code}/{target}"))
            await browser.close()
            return statuses, failed
    try:
        statuses, failed = asyncio.run(main())
    finally:
        server.shutdown()
    assert statuses == ["blocked"] * 10
    assert not [h for h in hits if h[2] == "/arrived"]
    # Every POST body reached the approved org's own server exactly once, and no further.
    posts = [h for h in hits if h[0] == "POST"]
    assert len(posts) == 10 and all(h[1] == "acme--sbx.sandbox.my.salesforce.com" and h[3] == b"secret-body"
                                    for h in posts)
    for target in TARGETS:
        assert any(target in url and "NAME_NOT_RESOLVED" in str(why) for url, why in failed), target
