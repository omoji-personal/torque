"""R2g: no cached authorization for any request, with the production recheck. Written
failing first."""
import asyncio
import io
import os
import time
from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace

import pytest

from torque import approval, browser_guard as bg, changes, consent, workspace as ws
from torque.presence import Presence

APPROVED = "https://acme--sbx.sandbox.lightning.force.com"
SBX_KEY = ("acme--sbx", "sandbox")


class Response:
    def __init__(self, status, location=None):
        self.status = status
        self.headers = {"location": location} if location else {}


class Route:
    """A Playwright route whose fetch answers from a table and records every hop."""

    def __init__(self, table):
        self.table = table
        self.fetched = []
        self.result = None
        self.fulfilled = None

    async def fetch(self, url=None, method=None, headers=None, post_data=None, max_redirects=None):
        assert max_redirects == 0, "redirects must never be followed by the fetch itself"
        self.fetched.append((method, url))
        return self.table[url]

    async def fulfill(self, response=None, status=None, headers=None, **kw):
        self.result = "fulfill"
        self.fulfilled = response if response is not None else Response(status, (headers or {}).get("location"))

    async def continue_(self):
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


def request(url, method="POST", navigation=False):
    return SimpleNamespace(url=url, method=method, headers={"content-type": "text/plain"}, post_data="x",
                           is_navigation_request=lambda: navigation)


def run(guard, route, req):
    async def main():
        context = Context()
        await bg.install(context, guard)
        await context.handler(route, req)
        return route
    return asyncio.run(main())


def guard():
    return bg.Guard(org_alias="acme-sbx", org_id_18="00D000000000001AAA", host_key=SBX_KEY,
                    expires_at=time.time() + 600, recheck=lambda: True)


# D2's manual redirect loop was removed in R2h (see test_connected_r8.py); the
# browser follows redirects natively and the resolver rules enforce every hop.


# N7: no cache for any request, with the production recheck

Org = namedtuple("Org", "org_id_18 detected_org_type is_production instance_url")
ORGS = {"acme-sbx": Org("00D000000000001AAA", "sandbox", False, "https://acme--sbx.sandbox.my.salesforce.com")}
YES = lambda: Presence(True, "")


@pytest.fixture
def connected(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr("jsc_revert.intent_marker._current_user_name", lambda: "consultant")
    root = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(root, "Acme")
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    letter = tmp_path / "a.pdf"
    letter.write_bytes(b"agreement")
    consent.record_consent(root, "Acme", "2026-09-30", letter, ["metadata"], ["acme-sbx"], ["Contact"], presence=YES,
                           resolve=ORGS.get)
    consent.sign_off(root, "Acme", "Reviewer", presence=YES)
    root = Path(os.path.realpath(root))
    monkeypatch.setenv("TORQUE_WORKSPACE", str(root / "clients" / "acme"))
    monkeypatch.setattr("jsc_revert.org_detect.resolve_org", lambda alias, **k: ORGS.get(alias))
    cid = changes.create_change(root, "Acme", "Layout", "Tier", [], "acme-sbx")["id"]
    req = approval.create_request(root, "Acme", cid, "acme-sbx", browser_minutes=10,
                                  purpose="Add Tier to the Case layout", resolve=ORGS.get)
    item = approval.grant(root, "Acme", req["id"], presence=YES, confirm=lambda: True, out=io.StringIO(),
                          resolve=ORGS.get)
    return root, item


def _two_reads(guard_obj, change):
    async def main():
        context = Context()
        await bg.install(context, guard_obj)
        first = Route({APPROVED + "/page": Response(200)})
        await context.handler(first, request(APPROVED + "/page", method="GET"))
        change()
        second = Route({APPROVED + "/page": Response(200)})
        await context.handler(second, request(APPROVED + "/page", method="GET"))
        await asyncio.sleep(0)
        return first.result, second.result, context.closed
    return asyncio.run(main())


def test_n7_a_read_right_after_suspension_is_refused(connected):
    root, _ = connected
    live = bg.connected_guard("acme-sbx")
    assert _two_reads(live, lambda: consent.suspend(root, "Acme", presence=YES)) == ("continue", "abort", True)


def test_n7_a_read_right_after_the_window_is_withdrawn_is_refused(connected):
    root, item = connected
    live = bg.connected_guard("acme-sbx")
    path = root / "clients" / "acme" / "approvals" / "granted" / f"{item['id']}.json"
    assert _two_reads(live, lambda: path.unlink()) == ("continue", "abort", True)
