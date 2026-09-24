"""R2i: the connected browser's resolver denies every host except the approved org's exact
hosts and the static content host. Trailing-dot forms and non-Salesforce hosts do not
resolve. Written failing first."""
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
APPROVED_HOST = "acme--sbx.sandbox.my.salesforce.com"


def resolves(rules: str, host: str) -> bool:
    """How Chromium reads the rules: an EXCLUDE pattern keeps a host out of every MAP
    rule; otherwise the first matching MAP rule applies."""
    parts = [r.strip().split(" ") for r in rules.split(",")]
    if any(p[0] == "EXCLUDE" and fnmatchcase(host, p[1]) for p in parts):
        return True
    for p in parts:
        if p[0] == "MAP" and fnmatchcase(host, p[1]):
            return p[2] != "~NOTFOUND"
    return True


class Route:
    def __init__(self):
        self.result = None

    async def continue_(self, **kwargs):
        self.result = "continue"

    async def abort(self, reason=None):
        self.result = "abort"


class Context:
    handler = None
    closed = False

    async def route(self, pattern, handler):
        self.handler = handler

    async def close(self):
        self.closed = True


def _chromium():
    import os
    from pathlib import Path
    pw = pytest.importorskip("playwright.async_api")
    cache = Path.home() / "Library" / "Caches" / "ms-playwright"
    for root in (Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")), cache):
        found = sorted(root.glob("chromium_headless_shell-*/*/chrome-headless-shell")) if root.is_dir() else []
        if found:
            return pw, str(found[-1])
    pytest.skip("no Chromium build available")


def guard(key=SBX_KEY):
    return bg.Guard(org_alias="acme", org_id_18="00D000000000001AAA", host_key=key,
                    expires_at=time.time() + 600, recheck=lambda: True)


def test_rules_deny_every_host_last():
    rules = bg.resolver_rules(guard())
    assert rules.split(", ")[-1] == "MAP * ~NOTFOUND"


@pytest.mark.parametrize("key", [SBX_KEY, PROD_KEY])
def test_rules_exclude_exactly_the_org_and_static_hosts(key):
    g = guard(key)
    excluded = [r.split(" ")[1] for r in bg.resolver_rules(g).split(", ") if r.startswith("EXCLUDE ")]
    assert sorted(excluded) == sorted([*bg.org_hosts(g), *bg.STATIC_HOSTS])
    assert not [h for h in excluded if "*" in h or h.endswith(".")]


@pytest.mark.parametrize("host", ["example.com", "localhost", "127.0.0.1", "10.0.0.5", "attacker.test",
                                  "beta.my.salesforce.com.", APPROVED_HOST + ".", "static.lightning.force.com.",
                                  "c.sfdcstatic.com", "login.salesforce.com", "beta.salesforce-experience.com"])
def test_rules_block_other_hosts_and_trailing_dot_forms(host):
    assert not resolves(bg.resolver_rules(guard()), host)


@pytest.mark.parametrize("url", ["https://example.com/collect", "http://localhost:8080/", "https://10.0.0.5/",
                                 f"https://{APPROVED_HOST}./aura", "https://beta.my.salesforce.com./aura"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_handler_refuses_any_host_outside_the_list(url, method):
    async def main():
        context = Context()
        await bg.install(context, guard())
        route = Route()
        await context.handler(route, SimpleNamespace(url=url, method=method, headers={}, post_data=None,
                                                     is_navigation_request=lambda: False))
        return route.result
    assert asyncio.run(main()) == "abort"


def test_handler_lets_a_non_network_url_through():
    async def main():
        context = Context()
        await bg.install(context, guard())
        route = Route()
        await context.handler(route, SimpleNamespace(url="data:text/plain,x", method="GET", headers={},
                                                     post_data=None, is_navigation_request=lambda: False))
        return route.result
    assert asyncio.run(main()) == "continue"


BLOCKED_TARGETS = ["example.com", "localhost", "127.0.0.1", "beta.my.salesforce.com.", APPROVED_HOST + "."]


def test_real_browser_deny_all_and_the_static_residual():
    pw, exe = _chromium()
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _serve(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            hits.append((self.command, self.headers.get("Host", "").rsplit(":", 1)[0], self.path, body))
            if self.path.startswith("/r/"):
                self.send_response(307)
                self.send_header("Location", f"http://{self.path[3:]}:{port}/arrived")
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                page = b"<html>ok</html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Access-Control-Allow-Origin", "*")
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
    base = f"http://{APPROVED_HOST}:{port}"

    async def main():
        failed = []
        async with pw.async_playwright() as p:
            browser = await p.chromium.launch(executable_path=exe, **launch)
            context = await browser.new_context(**context_options)
            context.on("requestfailed", lambda r: failed.append((r.url, r.failure)))
            await bg.install(context, live)
            page = await context.new_page()
            await page.goto(base + "/start")
            post = ("u => fetch(u, {method: 'POST', body: 'secret-body'})"
                    ".then(r => r.status).catch(() => 'blocked')")
            blocked = [await page.evaluate(post, f"/r/{t}") for t in BLOCKED_TARGETS]
            static = await page.evaluate(post, "/r/static.lightning.force.com")
            await browser.close()
            return blocked, static, failed
    try:
        blocked, static, failed = asyncio.run(main())
    finally:
        server.shutdown()
    assert blocked == ["blocked"] * len(BLOCKED_TARGETS)
    for target in BLOCKED_TARGETS:
        assert any(f"//{target}:" in url and "NAME_NOT_RESOLVED" in str(why) for url, why in failed), target
    arrived = [h for h in hits if h[2] == "/arrived"]
    # The one documented residual: a redirected POST reaches the static content host.
    assert static == 200 and [h[1] for h in arrived] == ["static.lightning.force.com"]
