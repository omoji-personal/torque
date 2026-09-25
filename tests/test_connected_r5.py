"""Fix round 5: the final recheck's Critical items (D2 redirects, N5 recovery operation, N7
session lifetime). Written failing first."""
import asyncio
from collections import namedtuple
import io
import json
import os
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from torque import approval, browser_guard as bg, changes, consent, gate_connected as gc, workspace as ws
from torque.presence import Presence

Org = namedtuple("Org", "org_id_18 detected_org_type is_production instance_url")
ORGS = {"acme-prod": Org("00D000000000002AAA", "production", True, "https://acme.my.salesforce.com"),
        "acme-sbx": Org("00D000000000001AAA", "sandbox", False, "https://acme--sbx.sandbox.my.salesforce.com")}
YES = lambda: Presence(True, "")
CONFIG = {"ai_access": "connected", "approval": "required", "approval_verify": "hmac"}
SBX_KEY = ("acme--sbx", "sandbox")


@pytest.fixture
def w(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr("jsc_revert.intent_marker._current_user_name", lambda: "consultant")
    root = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(root, "Acme")
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    letter = tmp_path / "a.pdf"
    letter.write_bytes(b"agreement")
    consent.record_consent(root, "Acme", "2026-09-30", letter, ["metadata"], ["acme-prod", "acme-sbx"],
                           ["Contact"], presence=YES, resolve=ORGS.get)
    consent.sign_off(root, "Acme", "Reviewer", presence=YES)
    return Path(os.path.realpath(root))


def change(root, org="acme-sbx"):
    return changes.create_change(root, "Acme", "Change", "Works", [], org)["id"]


def grant(root, req):
    return approval.grant(root, "Acme", req["id"], presence=YES, confirm=lambda: True, out=io.StringIO(),
                          resolve=ORGS.get)


# D2: every redirect hop is checked before it is sent

def test_d2_resolver_rules_keep_other_orgs_unresolvable():
    rules = bg.resolver_rules(bg.Guard(org_alias="acme-sbx", org_id_18="00D000000000001AAA", host_key=SBX_KEY))
    assert "EXCLUDE acme--sbx.sandbox.my.salesforce.com" in rules
    assert "EXCLUDE acme--sbx.sandbox.lightning.force.com" in rules
    assert rules.endswith("MAP * ~NOTFOUND")  # deny-all since R2i
    assert "acme.my.salesforce.com" not in rules


def test_d2_launch_options_in_connected_mode():
    guard = bg.Guard(org_alias="acme-sbx", org_id_18="00D000000000001AAA", host_key=SBX_KEY)
    launch, context = bg.launch_options(guard)
    assert any(a.startswith("--host-resolver-rules=") for a in launch["args"]) and "--no-proxy-server" in launch["args"]
    assert context == {"service_workers": "block"}


def _chromium():
    pw = pytest.importorskip("playwright.async_api")
    cache = Path.home() / "Library" / "Caches" / "ms-playwright"
    for root in (Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")), cache):
        found = sorted(root.glob("chromium_headless_shell-*/*/chrome-headless-shell")) if root.is_dir() else []
        if found:
            return pw, str(found[-1])
    pytest.skip("no Chromium build available")


def test_d2_real_browser_never_reaches_another_org_through_a_redirect(tmp_path):
    pw, exe = _chromium()
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _serve(self):
            hits.append((self.command, self.headers.get("Host", "").split(":")[0], self.path))
            if self.path.startswith("/redirect"):
                self.send_response(307 if "307" in self.path else 302)
                self.send_header("Location", f"http://acme.my.salesforce.com:{port}/stolen")
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                body = b"<html>ok</html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        do_GET = _serve
        do_POST = _serve

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    guard = bg.Guard(org_alias="acme-sbx", org_id_18="00D000000000001AAA", host_key=SBX_KEY)
    launch, context_options = bg.launch_options(guard, approved_target="127.0.0.1")
    base = f"http://acme--sbx.sandbox.my.salesforce.com:{port}"

    async def main():
        async with pw.async_playwright() as p:
            browser = await p.chromium.launch(executable_path=exe, **launch)
            context = await browser.new_context(**context_options)
            await bg.install(context, guard)
            page = await context.new_page()
            await page.goto(base + "/start")
            with pytest.raises(Exception):
                await page.goto(base + "/redirect", timeout=5000)
            page = await context.new_page()
            await page.goto(base + "/start")
            status = await page.evaluate("fetch('/redirect307', {method: 'POST', body: 'x'})"
                                         ".then(r => r.status).catch(() => 'blocked')")
            await browser.close()
            return status
    try:
        status = asyncio.run(main())
    finally:
        server.shutdown()
    assert status == "blocked"
    assert not [h for h in hits if h[1] == "acme.my.salesforce.com"], hits
    assert [h for h in hits if h[1] == "acme--sbx.sandbox.my.salesforce.com"]


def test_d2_attached_operator_browser_is_refused_in_connected_mode(monkeypatch):
    from types import SimpleNamespace
    from jsc_browser_tests import auth
    guard = bg.Guard(org_alias="acme-sbx", org_id_18="00D000000000001AAA", host_key=SBX_KEY)
    monkeypatch.setattr(bg, "connected_guard", lambda target: guard)
    admin = SimpleNamespace(target_org="acme-sbx", frontdoor_url="x", instance_url="x")
    with pytest.raises(auth.AuthError, match="attached"):
        asyncio.run(auth.open_session(SimpleNamespace(chromium=None), admin, cdp_endpoint="http://127.0.0.1:9"))


# N7: the session stops when its window ends or consent is suspended

class FakeRoute:
    def __init__(self):
        self.result = None

    async def continue_(self):
        self.result = "continue"

    async def abort(self, reason=None):
        self.result = "abort"


class FakeContext:
    def __init__(self):
        self.handler = None
        self.closed = False

    async def route(self, pattern, handler):
        self.handler = handler

    async def close(self):
        self.closed = True


def _request(url="https://acme--sbx.sandbox.lightning.force.com/aura", method="POST"):
    from types import SimpleNamespace
    return SimpleNamespace(url=url, method=method)


def test_n7_window_expiry_stops_the_session():
    guard = bg.Guard(org_alias="acme-sbx", org_id_18="00D000000000001AAA", host_key=SBX_KEY,
                     expires_at=time.time() - 1, recheck=lambda: True)

    async def main():
        context = FakeContext()
        await bg.install(context, guard)
        route = FakeRoute()
        await context.handler(route, _request())
        await asyncio.sleep(0)
        return route.result, context.closed
    result, closed = asyncio.run(main())
    assert result == "abort" and closed and guard.stopped


def test_n7_suspension_stops_the_session():
    allowed = {"ok": True}
    guard = bg.Guard(org_alias="acme-sbx", org_id_18="00D000000000001AAA", host_key=SBX_KEY,
                     expires_at=time.time() + 600, recheck=lambda: allowed["ok"], recheck_every=0)

    async def main():
        context = FakeContext()
        await bg.install(context, guard)
        first = FakeRoute()
        await context.handler(first, _request())
        allowed["ok"] = False
        second, third = FakeRoute(), FakeRoute()
        await context.handler(second, _request())
        allowed["ok"] = True
        await context.handler(third, _request(method="GET"))
        await asyncio.sleep(0)
        return first.result, second.result, third.result, context.closed
    assert asyncio.run(main()) == ("continue", "abort", "abort", True)


def test_n7_connected_guard_carries_its_window_and_rechecks(w, monkeypatch):
    monkeypatch.setenv("TORQUE_WORKSPACE", str(w / "clients" / "acme"))
    monkeypatch.setattr("jsc_revert.org_detect.resolve_org", lambda alias, **k: ORGS.get(alias))
    req = approval.create_request(w, "Acme", change(w), "acme-sbx", browser_minutes=10,
                                  purpose="Add Tier to the Case layout", resolve=ORGS.get)
    item = grant(w, req)
    guard = bg.connected_guard("acme-sbx")
    assert guard.expires_at == approval._epoch(item["expires_at"])
    assert guard.recheck()
    consent.suspend(w, "Acme", presence=YES)
    assert not guard.recheck()


# N5: the recovery operation is bound

def _fake_snapshot(tmp_path, monkeypatch, plan=("deploy", "-o", "acme-sbx")):
    snap = tmp_path / "snap"
    snap.mkdir(exist_ok=True)
    (snap / "manifest.json").write_text('{"payload": {"record_id": "001A"}}', encoding="utf-8")
    monkeypatch.setattr(approval, "_recovery_snapshot",
                        lambda workspace, client, snapshot_id, org_alias, org_id: (snap, list(plan)))
    return snap


def test_n5_options_before_the_subcommand_are_still_bound(w, tmp_path, monkeypatch):
    _fake_snapshot(tmp_path, monkeypatch)
    for argv in (["torque", "recover", "--workspace", str(w), "--client", "acme", "exec", "0123456789abcdef",
                  "--org", "acme-sbx"],
                 ["torque", "revert", "--workspace", str(w), "--client", "acme", "revert", "exec",
                  "0123456789abcdef", "--org", "acme-sbx"],
                 ["torque", "recover", "run", "0123456789abcdef", "--org", "acme-sbx", "--workspace", str(w),
                  "--client", "acme"]):
        req = approval.create_request(w, "Acme", change(w), "acme-sbx", argv=argv, resolve=ORGS.get, cwd=tmp_path)
        assert req["recovery_snapshot"] == "0123456789abcdef" and req["payload_files"] >= 1, argv


def test_n5_an_unparsable_recovery_command_is_refused(w, tmp_path):
    with pytest.raises(ws.WorkspaceError, match="recovery"):
        approval.create_request(w, "Acme", change(w), "acme-sbx",
                                argv=["torque", "recover", "exec", "--org", "acme-sbx"], resolve=ORGS.get,
                                cwd=tmp_path)


def test_n5_execution_must_use_the_approved_snapshot_and_operation(tmp_path):
    snap = tmp_path / "snap"
    snap.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    approved = {"recovery_snapshot": "0123456789abcdef", "recovery_snapshot_dir": os.path.realpath(snap),
                "recovery_plan": ["deploy", "-o", "acme-sbx", "--metadata", "ApexClass:A"]}
    plan = list(approved["recovery_plan"])
    assert approval.recovery_problem(approved, snap, plan) == ""
    assert "snapshot" in approval.recovery_problem(approved, other, plan)
    assert "operation" in approval.recovery_problem(approved, snap, ["deploy", "-o", "acme-sbx", "--metadata",
                                                                     "ApexClass:B"])
    assert approval.recovery_problem({"recovery_snapshot": None}, snap, plan)


def test_n5_executor_refuses_a_replaced_snapshot_directory(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from jsc_revert import revert_executor as ex
    from jsc_revert.wrappers import _common
    approved_dir, later_dir = tmp_path / "20260924-aaaa", tmp_path / "20260925-aaaa"
    approved_dir.mkdir()
    later_dir.mkdir()
    snap = {"org": {"org_id_18": "00D000000000001AAA", "alias": "acme-sbx"}, "operation_type": "data_create",
            "snapshot_id": "0123456789abcdef"}
    monkeypatch.setattr(ex.org_detect, "resolve_org", lambda alias: SimpleNamespace(
        org_id_short="00D000000000001", org_id_18="00D000000000001AAA", alias=alias))
    monkeypatch.setattr(ex.mf, "load_by_id", lambda *a: (later_dir, snap))
    monkeypatch.setattr(ex.revert_capabilities, "effective_capabilities", lambda s: {})
    monkeypatch.setattr(ex.revert_planner, "build_revert_command", lambda s, d: ["jsc", "data", "delete", "-o",
                                                                               "acme-sbx", "--record-id", "001B"])
    approved = {"id": "apr-000000000001", "recovery_snapshot": "0123456789abcdef",
                "recovery_snapshot_dir": os.path.realpath(approved_dir),
                "recovery_plan": ["data", "delete", "-o", "acme-sbx", "--record-id", "001A"], "_scope": (tmp_path, "acme")}
    monkeypatch.setattr(_common, "connected_approval", lambda org, org_id: (0, approved))
    monkeypatch.setattr(ex.subprocess, "run", lambda *a, **k: pytest.fail("the recovery must not run"))
    assert ex.execute_revert("0123456789abcdef", "acme-sbx") == ex.EXIT_ORG_RESOLUTION_FAILED
