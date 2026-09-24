"""R2g: every redirect hop checked by Torque (method and destination), and no cached
authorization for any request. Written failing first."""
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


@pytest.mark.parametrize("status", [307, 308])
@pytest.mark.parametrize("shared", ["https://login.salesforce.com/", "https://test.salesforce.com/x"])
def test_d2_post_redirect_to_a_shared_host_is_refused(status, shared):
    route = Route({APPROVED + "/aura": Response(status, shared)})
    run(guard(), route, request(APPROVED + "/aura"))
    assert route.result == "abort" and route.fetched == [("POST", APPROVED + "/aura")]


def test_d2_post_redirect_to_another_org_is_refused():
    route = Route({APPROVED + "/aura": Response(307, "https://acme.my.salesforce.com/aura")})
    run(guard(), route, request(APPROVED + "/aura"))
    assert route.result == "abort" and len(route.fetched) == 1


def test_d2_303_turns_a_post_into_a_get_to_a_shared_host():
    route = Route({APPROVED + "/save": Response(303, "https://login.salesforce.com/done"),
                   "https://login.salesforce.com/done": Response(200)})
    run(guard(), route, request(APPROVED + "/save"))
    assert route.fetched == [("POST", APPROVED + "/save"), ("GET", "https://login.salesforce.com/done")]
    assert route.result == "fulfill" and route.fulfilled.status == 200


def test_d2_a_chain_inside_the_org_is_followed_and_every_hop_checked():
    route = Route({APPROVED + "/a": Response(302, "/b"), APPROVED + "/b": Response(307, APPROVED + "/c"),
                   APPROVED + "/c": Response(200)})
    run(guard(), route, request(APPROVED + "/a", method="GET"))
    assert [u for _, u in route.fetched] == [APPROVED + "/a", APPROVED + "/b", APPROVED + "/c"]
    assert route.result == "fulfill" and route.fulfilled.status == 200


def test_d2_a_chain_that_leaves_later_is_refused():
    route = Route({APPROVED + "/a": Response(302, APPROVED + "/b"),
                   APPROVED + "/b": Response(302, "https://beta.my.salesforce.com/")})
    run(guard(), route, request(APPROVED + "/a", method="GET"))
    assert route.result == "abort" and len(route.fetched) == 2


def test_d2_navigation_redirect_is_handed_to_the_browser_at_its_checked_end():
    route = Route({APPROVED + "/a": Response(302, APPROVED + "/b"), APPROVED + "/b": Response(200)})
    run(guard(), route, request(APPROVED + "/a", method="GET", navigation=True))
    assert route.result == "fulfill" and route.fulfilled.status == 302
    assert route.fulfilled.headers["location"] == APPROVED + "/b"


def test_d2_too_many_hops_is_refused():
    table = {APPROVED + f"/{i}": Response(302, APPROVED + f"/{i + 1}") for i in range(30)}
    route = Route(table)
    run(guard(), route, request(APPROVED + "/0", method="GET"))
    assert route.result == "abort"


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
    assert _two_reads(live, lambda: consent.suspend(root, "Acme", presence=YES)) == ("fulfill", "abort", True)


def test_n7_a_read_right_after_the_window_is_withdrawn_is_refused(connected):
    root, item = connected
    live = bg.connected_guard("acme-sbx")
    path = root / "clients" / "acme" / "approvals" / "granted" / f"{item['id']}.json"
    assert _two_reads(live, lambda: path.unlink()) == ("fulfill", "abort", True)
