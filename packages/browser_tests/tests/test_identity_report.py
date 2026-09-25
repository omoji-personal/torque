"""The browser route reports who the browser was, as observed in the page, and never
the session URL, the session ID or a cookie."""
import asyncio
import json
import sys
import types
from unittest.mock import AsyncMock

import pytest

from jsc_browser_tests import auth, cli, runner
from jsc_browser_tests.diagnostics import redact
from jsc_browser_tests.flow_spec import FlowSpec

ORG = "00D000000000003AAA"
OTHER_ORG = "00D000000000009AAA"
ADMIN = "005000000000001AAA"
USER = "005000000000002AAA"
SID = "00Dsynthetic!secret"
SECRET = f"https://x.my.salesforce.com/secur/frontdoor.jsp?sid={SID}"


def fake_playwright(monkeypatch):
    class CM:
        async def __aenter__(self): return object()
        async def __aexit__(self, *args): pass
    api = types.ModuleType("playwright.async_api")
    api.async_playwright = lambda: CM()
    monkeypatch.setitem(sys.modules, "playwright.async_api", api)


class Browser:
    """The page's identity as the live browser shows it: admin until Login As, the
    requested user after it, admin again after Logout As."""
    def __init__(self, org=ORG, login_error=None):
        self.user, self.org, self.login_error = ADMIN, org, login_error

    async def observe_user(self, page):
        return self.user

    async def observe_org(self, page):
        if isinstance(self.org, Exception):
            raise self.org
        return self.org

    async def login_as(self, page, instance_url, user_id, org_id_18=None):
        if self.login_error:
            raise self.login_error
        self.user = user_id

    async def logout(self, page, instance_url):
        self.user = ADMIN


def stub(monkeypatch, *, org=ORG, login_error=None, guarded=False):
    fake_playwright(monkeypatch)
    browser = Browser(org=org, login_error=login_error)
    page = types.SimpleNamespace(wait_for_selector=AsyncMock())
    opened = AsyncMock(return_value=types.SimpleNamespace(page=page, guarded=guarded))
    monkeypatch.setattr(auth, "get_admin_auth", lambda *_: types.SimpleNamespace(
        instance_url="https://x.my.salesforce.com", org_id_18=ORG, frontdoor_url=SECRET))
    monkeypatch.setattr(auth, "open_session", opened)
    monkeypatch.setattr(auth, "close_session", AsyncMock())
    monkeypatch.setattr(auth, "login_as_user", browser.login_as)
    monkeypatch.setattr(auth, "observe_user_id", browser.observe_user)
    monkeypatch.setattr(auth, "observe_org_id", browser.observe_org)
    monkeypatch.setattr(auth, "logout_as_user", browser.logout)
    return opened


def fake_cell(monkeypatch, *, leak=False):
    """run_cell as a flow that leaks the session URL into its step detail, its error and
    a screenshot file would; the runner must not pass any of it on."""
    import jsc_browser_tests.cell_runner as cell_runner
    ran = []

    async def run_cell(flow, ctx):
        ran.append(ctx.profile)
        result = runner.FlowResult(flow_name="visit", profile=ctx.profile, target_org=ctx.target_org,
                                   overall_status="PASS")
        if leak:
            (ctx.run_dir / f"{ctx.profile}.png").write_bytes(b"\x89PNG synthetic pixels")
            result.steps.append(runner.StepResult("lightning_url_reached", "FAIL", "USER_FIDELITY",
                                                  detail=f"current URL: {SECRET}"))
            result.error = f"navigation failed at {SECRET}"
            result.side_effects = {"last_url": SECRET,
                                   "next": f"https://x.lightning.force.com/one/one.app?retURL=%2Fhome%3Fsid%3D{SID}"}
            result.overall_status = "FAIL"
        return result
    monkeypatch.setattr(cell_runner, "run_cell", run_cell)
    return ran


def run(tmp_path, profile="standard"):
    flow = types.SimpleNamespace(name="visit")
    user = None if profile == "admin" else {"user_id": USER}
    return asyncio.run(runner.run_flow_variation(flow, runner.Variation("steward", profile), "acme-dev", tmp_path,
                                                 test_user=user, sf_client=object()))


def assert_clean(text):
    for needle in ("frontdoor.jsp", "sid=", "sid%3D", SID, "secur/"):
        assert needle not in text, needle


def test_identity_report_names_org_users_and_restoration(monkeypatch, tmp_path):
    opened = stub(monkeypatch)
    ran = fake_cell(monkeypatch)
    result = run(tmp_path)
    assert ran == ["standard"]
    assert runner.identity_report(result) == {"org_id_18": ORG, "admin_before": ADMIN, "user_after_login_as": USER,
                                              "admin_restored": ADMIN, "restored": True, "status": "MATCHED"}
    assert opened.await_args.kwargs.get("headed") is False
    assert cli._result_to_dict(result)["identity"]["org_id_18"] == ORG


def test_identity_org_is_the_one_the_page_shows_not_the_argument(monkeypatch, tmp_path):
    """The report names the org the page is in; a page in another org stops the run."""
    stub(monkeypatch, org=OTHER_ORG, guarded=True)
    ran = fake_cell(monkeypatch)
    result = run(tmp_path)
    assert ran == []
    assert result.overall_status == "INCOMPLETE"
    report = runner.identity_report(result)
    assert report["org_id_18"] == OTHER_ORG and report["status"] == "ORG_MISMATCH"
    assert report["restored"] is True


def test_guarded_route_fails_closed_when_the_page_org_cannot_be_read(monkeypatch, tmp_path):
    stub(monkeypatch, org=auth.AuthError("Browser org could not be observed"), guarded=True)
    ran = fake_cell(monkeypatch)
    result = run(tmp_path)
    assert ran == [] and result.overall_status == "INCOMPLETE"
    report = runner.identity_report(result)
    assert report["org_id_18"] is None and report["status"] == "ORG_NOT_CHECKED"


def test_unguarded_route_keeps_running_and_says_the_org_was_not_checked(monkeypatch, tmp_path):
    """Outside connected mode the a15 behavior stands; the report does not claim a match."""
    stub(monkeypatch, org=auth.AuthError("Browser org could not be observed"))
    ran = fake_cell(monkeypatch)
    result = run(tmp_path)
    assert ran == ["standard"] and result.overall_status == "PASS"
    assert runner.identity_report(result)["status"] == "ORG_NOT_CHECKED"


def test_admin_report_has_no_login_as_user(monkeypatch, tmp_path):
    stub(monkeypatch, guarded=True)
    fake_cell(monkeypatch)
    result = run(tmp_path, profile="admin")
    assert runner.identity_report(result) == {"org_id_18": ORG, "admin_before": ADMIN, "user_after_login_as": None,
                                              "admin_restored": None, "restored": False, "status": "OBSERVED"}


def test_no_session_url_in_any_output(monkeypatch, tmp_path, capsys):
    stub(monkeypatch, login_error=RuntimeError(f"navigation failed at {SECRET}"))
    fake_cell(monkeypatch)
    result = run(tmp_path)
    text = json.dumps(cli._result_to_dict(result)) + str(result.error)
    cli._print_flow_result(result)
    text += capsys.readouterr().out
    assert_clean(text)


def test_json_route_leaves_no_session_url_in_stdout_or_any_run_file(monkeypatch, tmp_path, capsys):
    """`multiprofile --json` end to end with fakes: the JSON on stdout, the manifest, the
    audit log and every other file in the run dir are scanned for the URL and the sid."""
    monkeypatch.setenv("TORQUE_WORKSPACE", str(tmp_path / "client"))
    opened = stub(monkeypatch)
    fake_cell(monkeypatch, leak=True)
    import jsc_browser_tests.matrix as matrix
    monkeypatch.setattr(matrix, "login_as_preflight", AsyncMock(return_value={"standard": "PASS"}))
    monkeypatch.setattr(cli, "_load_seed", lambda alias: {"alias": alias, "users": {"standard": {"user_id": USER}}})
    flow = types.SimpleNamespace(name="visit", spec=FlowSpec(
        name="visit", workflow="E1", writes=False, profiles=["admin", "standard"],
        variations=[runner.Variation("happy", profile="admin")]))
    monkeypatch.setattr(cli, "_load_library_flow", lambda name: flow)

    code = cli.main(["multiprofile", "visit", "--target-org", "acme-dev", "--json"])
    out = capsys.readouterr()
    cells = json.loads(out.out)
    assert code != 0 and [c["profile"] for c in cells] == ["admin", "standard"]
    assert cells[1]["identity"]["user_after_login_as"] == USER and cells[1]["identity"]["org_id_18"] == ORG
    assert all(call.kwargs.get("headed") is False for call in opened.await_args_list)
    text = out.out + out.err
    files = [p for p in (tmp_path / "client").rglob("*") if p.is_file()]
    assert any(p.name == "manifest.json" for p in files) and any(p.suffix == ".png" for p in files)
    for path in files:
        text += path.read_bytes().decode("utf-8", "replace")
    assert_clean(text)


def test_page_org_observer_returns_only_a_valid_org_id():
    frames = [types.SimpleNamespace(evaluate=AsyncMock(return_value=None)),
              types.SimpleNamespace(evaluate=AsyncMock(return_value=ORG))]
    page = types.SimpleNamespace(frames=frames)
    assert asyncio.run(auth.observe_org_id(page)) == ORG
    for value in (None, SID, "005000000000001AAA", ORG + "!x", 42):
        frames = [types.SimpleNamespace(evaluate=AsyncMock(return_value=value))]
        with pytest.raises(auth.AuthError) as info:
            asyncio.run(auth.observe_org_id(types.SimpleNamespace(frames=frames)))
        assert SID not in str(info.value)
    broken = types.SimpleNamespace(frames=[types.SimpleNamespace(evaluate=AsyncMock(side_effect=RuntimeError(SECRET)))])
    with pytest.raises(auth.AuthError) as info:
        asyncio.run(auth.observe_org_id(broken))
    assert_clean(str(info.value))


class FakeContext:
    def __init__(self, options):
        self.options, self.closed = options, False

    async def new_page(self):
        async def goto(url, **kwargs):
            raise RuntimeError(f"net::ERR at {url}")
        return types.SimpleNamespace(goto=goto)

    async def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self):
        self.launches, self.contexts = [], []

    async def launch(self, **kwargs):
        self.launches.append(kwargs)
        chromium = self

        class Browser:
            async def new_context(self, **options):
                chromium.contexts.append(FakeContext(options))
                return chromium.contexts[-1]

            async def close(self):
                pass
        return Browser()


def guarded_session(monkeypatch, *, delegated, headed):
    from torque import browser_guard
    guard = browser_guard.Guard(org_alias="acme-dev", org_id_18=ORG, host_key=("acme-dev", "develop"),
                                delegated=delegated)
    monkeypatch.setattr(browser_guard, "connected_guard", lambda alias: guard)
    installed = AsyncMock()
    monkeypatch.setattr(browser_guard, "install", installed)
    pw = types.SimpleNamespace(chromium=FakeChromium())
    admin = auth.AdminAuth("acme-dev", ORG, "https://acme-dev.develop.my.salesforce.com", SECRET)
    with pytest.raises(auth.AuthError) as info:
        asyncio.run(auth.open_session(pw, admin, headed=headed))
    return pw.chromium, installed, str(info.value)


def test_delegated_window_refuses_a_visible_browser_before_launching(monkeypatch):
    chromium, _, message = guarded_session(monkeypatch, delegated=True, headed=True)
    assert chromium.launches == [] and "headless" in message
    assert_clean(message)


def test_delegated_window_launches_headless_with_no_trace_har_or_video(monkeypatch):
    chromium, installed, message = guarded_session(monkeypatch, delegated=True, headed=False)
    assert [launch["headless"] for launch in chromium.launches] == [True]
    options = chromium.contexts[0].options
    assert not [key for key in options if key.startswith("record_")]
    installed.assert_awaited_once()
    assert chromium.contexts[0].closed
    assert_clean(message)


def test_human_window_may_still_open_a_visible_browser(monkeypatch):
    chromium, _, _ = guarded_session(monkeypatch, delegated=False, headed=True)
    assert [launch["headless"] for launch in chromium.launches] == [False]


@pytest.mark.parametrize("text", [
    SECRET,
    f"see https://x.my.salesforce.com/secur/frontdoor.jsp?retURL=%2Flightning&sid={SID} now",
    f"https://x.my.salesforce.com/secur/frontdoor.jsp?sid={SID}&retURL=%2Fhome",
    f"https://x.lightning.force.com/one/one.app?retURL=%2Fhome%3Fsid%3D{SID}",
    f"https://x.my.salesforce.com/home?sid%3D{SID}",
    f"https%3A%2F%2Fx.my.salesforce.com%2Fsecur%2Ffrontdoor.jsp%3Fsid%3D{SID}",
    f"frontdoor.jsp?sid={SID}",
    f"'{SECRET}'",
])
def test_session_urls_are_removed_whole(text):
    out = redact(text)
    assert_clean(out)
    assert "[redacted session URL]" in out


def test_ordinary_urls_are_left_alone():
    url = "https://x.lightning.force.com/lightning/r/Contact/003000000000001AAA/view"
    assert redact(url) == url
