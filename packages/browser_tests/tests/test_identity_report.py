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
ADMIN = "005000000000001AAA"
USER = "005000000000002AAA"
ADMIN_NAME = "admin@acme-dev.example"
USERNAMES = {ADMIN: "Admin@Acme-Dev.example", USER: "steward@acme-dev.example"}
SID = "00Dsynthetic!secret"
SECRET = f"https://x.my.salesforce.com/secur/frontdoor.jsp?sid={SID}"


def fake_playwright(monkeypatch):
    class CM:
        async def __aenter__(self): return object()
        async def __aexit__(self, *args): pass
    api = types.ModuleType("playwright.async_api")
    api.async_playwright = lambda: CM()
    monkeypatch.setitem(sys.modules, "playwright.async_api", api)


class Page:
    """A signed-in Lightning page: Aura's $A gives the current User Id, and the page's own
    same-origin UI API read gives that user's Username (or an HTTP error). Login As and
    Logout As change the current user."""
    def __init__(self, *, aura=True, ui_status=200, usernames=USERNAMES, login_error=None):
        self.user, self.aura, self.ui_status = ADMIN, aura, ui_status
        self.usernames, self.login_error, self.fetched = usernames, login_error, []

    async def wait_for_selector(self, *args, **kwargs):
        pass

    async def wait_for_function(self, script, timeout=None):
        if not self.aura:
            raise TimeoutError("Timeout 10000ms exceeded")

    async def evaluate(self, script, arg=None):
        if "ui-api/records/" in script:
            version, user_id = arg
            self.fetched.append((version, user_id))
            if self.ui_status != 200:
                return {"status": self.ui_status, "username": None}
            return {"status": 200, "username": self.usernames.get(user_id)}
        if "CurrentUser.Id" in script:
            return self.user if self.aura else None
        raise AssertionError("unexpected page script")

    async def login_as(self, page, instance_url, user_id, org_id_18=None):
        if self.login_error:
            raise self.login_error
        self.user = user_id

    async def logout(self, page, instance_url):
        self.user = ADMIN


def stub(monkeypatch, *, guarded=False, username=ADMIN_NAME, **page_options):
    fake_playwright(monkeypatch)
    page = Page(**page_options)
    opened = AsyncMock(return_value=types.SimpleNamespace(page=page, guarded=guarded))
    monkeypatch.setattr(auth, "get_admin_auth", lambda *_: auth.AdminAuth(
        "acme-dev", ORG, "https://x.my.salesforce.com", SECRET, username=username, api_version="67.0"))
    monkeypatch.setattr(auth, "open_session", opened)
    monkeypatch.setattr(auth, "close_session", AsyncMock())
    monkeypatch.setattr(auth, "login_as_user", page.login_as)
    monkeypatch.setattr(auth, "logout_as_user", page.logout)
    return opened, page


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


VERIFIED = {"org_id_18": ORG, "org_verified_by": "username", "admin_before": ADMIN,
            "admin_username": USERNAMES[ADMIN]}


@pytest.mark.parametrize("guarded", [False, True])
def test_identity_report_names_org_users_and_restoration(monkeypatch, tmp_path, guarded):
    opened, page = stub(monkeypatch, guarded=guarded)
    ran = fake_cell(monkeypatch)
    result = run(tmp_path)
    assert ran == ["standard"]
    assert runner.identity_report(result) == {**VERIFIED, "user_after_login_as": USER,
                                              "admin_restored": ADMIN, "restored": True, "status": "MATCHED"}
    assert page.fetched == [("67.0", ADMIN)]  # the admin's username, read before Login As
    assert opened.await_args.kwargs.get("headed") is False
    assert cli._result_to_dict(result)["identity"]["org_id_18"] == ORG


def test_admin_report_has_no_login_as_user(monkeypatch, tmp_path):
    stub(monkeypatch, guarded=True)
    fake_cell(monkeypatch)
    result = run(tmp_path, profile="admin")
    assert runner.identity_report(result) == {**VERIFIED, "user_after_login_as": None,
                                              "admin_restored": None, "restored": False, "status": "OBSERVED"}


FAILURES = {
    "no $A": dict(aura=False),
    "UI API 401": dict(ui_status=401),
    "UI API 500": dict(ui_status=500),
    "no Username field": dict(usernames={}),
    "sf knows no username": dict(username=""),
}


@pytest.mark.parametrize("case", sorted(FAILURES))
def test_guarded_route_fails_closed_when_the_username_cannot_be_read(monkeypatch, tmp_path, case):
    stub(monkeypatch, guarded=True, **FAILURES[case])
    ran = fake_cell(monkeypatch)
    result = run(tmp_path)
    assert ran == [] and result.overall_status == "INCOMPLETE"
    report = runner.identity_report(result)
    assert report["org_id_18"] is None and report["org_verified_by"] is None
    assert report["status"] in ("ORG_NOT_CHECKED", "NOT_CHECKED")  # NOT_CHECKED: no user at all (no $A)


@pytest.mark.parametrize("guarded", [False, True])
def test_a_username_mismatch_stops_a_guarded_run_and_is_reported_elsewhere(monkeypatch, tmp_path, guarded):
    """Another org (or another user) behind the page: its username is not the alias's.
    A connected run stops; elsewhere the a15 behavior stands and the report says so."""
    stub(monkeypatch, guarded=guarded, username="admin@acme-prod.example")
    ran = fake_cell(monkeypatch)
    result = run(tmp_path)
    if guarded:
        assert ran == [] and result.overall_status == "INCOMPLETE"
    else:
        assert ran == ["standard"] and result.overall_status == "PASS"
    report = runner.identity_report(result)
    assert report["org_id_18"] is None and report["status"] == "ORG_MISMATCH"


@pytest.mark.parametrize("case", ["UI API 401", "no Username field", "sf knows no username"])
def test_unguarded_route_keeps_running_and_says_the_org_was_not_checked(monkeypatch, tmp_path, case):
    """Outside connected mode the a15 behavior stands; the report does not claim a match."""
    stub(monkeypatch, **FAILURES[case])
    ran = fake_cell(monkeypatch)
    result = run(tmp_path)
    assert ran == ["standard"] and result.overall_status == "PASS"
    assert runner.identity_report(result)["status"] == "ORG_NOT_CHECKED"


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
    opened, _ = stub(monkeypatch, guarded=True)
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
    identity = cells[1]["identity"]
    assert identity["user_after_login_as"] == USER and identity["org_id_18"] == ORG
    assert identity["admin_username"] == USERNAMES[ADMIN] and identity["org_verified_by"] == "username"
    assert all(call.kwargs.get("headed") is False for call in opened.await_args_list)
    text = out.out + out.err
    files = [p for p in (tmp_path / "client").rglob("*") if p.is_file()]
    assert any(p.name == "manifest.json" for p in files) and any(p.suffix == ".png" for p in files)
    for path in files:
        text += path.read_bytes().decode("utf-8", "replace")
    assert_clean(text)


def test_username_reader_keeps_only_a_valid_username():
    page = Page()
    assert asyncio.run(auth.observe_username(page, ADMIN, "67.0")) == USERNAMES[ADMIN]
    assert asyncio.run(auth.observe_username(page, ADMIN, "not a version")) == USERNAMES[ADMIN]
    assert page.fetched[-1] == (auth.UI_API_VERSION, ADMIN)
    for value in ({"status": 200, "username": SID}, {"status": 200, "username": "no at sign"},
                  {"status": 200, "username": None}, {"status": 302, "username": ADMIN_NAME}, None, "text"):
        bad = types.SimpleNamespace(evaluate=AsyncMock(return_value=value))
        with pytest.raises(auth.AuthError) as info:
            asyncio.run(auth.observe_username(bad, ADMIN))
        assert SID not in str(info.value)
    broken = types.SimpleNamespace(evaluate=AsyncMock(side_effect=RuntimeError(SECRET)))
    with pytest.raises(auth.AuthError) as info:
        asyncio.run(auth.observe_username(broken, ADMIN))
    assert_clean(str(info.value))
    with pytest.raises(auth.AuthError):
        asyncio.run(auth.observe_username(page, "005'); alert(1); //", "67.0"))


def test_page_scripts_read_no_cookie_and_return_only_status_and_username():
    script = auth._PAGE_USERNAME_JS
    assert "cookie" not in script.casefold()
    assert "credentials: 'same-origin'" in script and "?fields=User.Username" in script
    assert "return {status: response.status, username:" in script


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


# Playwright debug output prints the navigated frontdoor URL (DEBUG=pw:api, pw:protocol,
# DEBUG_FILE), and PWDEBUG forces a visible browser: a connected run refuses to start.

# Node's debug module turns pw:* on for DEBUG=*, pw* or p* too, so any DEBUG is refused.
DEBUG_ENVS = [{"PWDEBUG": "1"}, {"DEBUG": "pw:api"}, {"DEBUG": "other,pw:protocol"}, {"DEBUG_FILE": "/tmp/pw.log"},
              {"DEBUG": "*"}, {"DEBUG": "pw*"}, {"DEBUG": "p*"}, {"DEBUG": "foo"}]


def clear_debug_env(monkeypatch):
    for name in ("PWDEBUG", "DEBUG", "DEBUG_FILE"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("env", DEBUG_ENVS)
def test_connected_run_refuses_playwright_debug_before_playwright_starts(monkeypatch, tmp_path, env):
    stub(monkeypatch, guarded=True)
    clear_debug_env(monkeypatch)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(auth, "in_connected_mode", lambda: True)
    started = []
    api = types.ModuleType("playwright.async_api")
    api.async_playwright = lambda: started.append(1)
    monkeypatch.setitem(sys.modules, "playwright.async_api", api)
    ran = fake_cell(monkeypatch)
    result = run(tmp_path)
    assert started == [] and ran == [] and result.overall_status == "INCOMPLETE"
    assert next(iter(env)) in result.error


@pytest.mark.parametrize("env", DEBUG_ENVS)
def test_unconnected_run_keeps_a15_debugging(monkeypatch, tmp_path, env):
    stub(monkeypatch)
    clear_debug_env(monkeypatch)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(auth, "in_connected_mode", lambda: False)
    ran = fake_cell(monkeypatch)
    assert run(tmp_path).overall_status == "PASS" and ran == ["standard"]


def test_no_debug_setting_is_no_problem(monkeypatch):
    clear_debug_env(monkeypatch)
    monkeypatch.setenv("DEBUG", "")
    assert auth.debug_env_problem() is None


def test_preflight_refuses_playwright_debug_in_connected_mode(monkeypatch):
    from jsc_browser_tests import suite
    clear_debug_env(monkeypatch)
    monkeypatch.setenv("PWDEBUG", "1")
    monkeypatch.setattr(auth, "in_connected_mode", lambda: True)
    monkeypatch.setattr(auth, "get_admin_auth", lambda *_: pytest.fail("no auth before the check"))
    api = types.ModuleType("playwright.async_api")
    api.async_playwright = lambda: pytest.fail("playwright must not start")
    monkeypatch.setitem(sys.modules, "playwright.async_api", api)
    config = {"profiles": ["admin", "standard"], "seed": {"users": {"standard": {"user_id": USER}}},
              "sf": object(), "target_org": "acme-dev"}
    with pytest.raises(auth.AuthError, match="PWDEBUG"):
        asyncio.run(suite._live_preflight(config))


@pytest.mark.parametrize("env", DEBUG_ENVS)
def test_guarded_session_refuses_playwright_debug_before_launching(monkeypatch, env):
    clear_debug_env(monkeypatch)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    chromium, _, message = guarded_session(monkeypatch, delegated=False, headed=False)
    assert chromium.launches == [] and next(iter(env)) in message


# Salesforce session tokens by value shape, and more credential names.

TOKEN = "00D000000000003!AQ8AQFakeSyntheticToken.value_x-y"


@pytest.mark.parametrize("text", [
    f'{{"status": 0, "result": {{"accessToken": "{TOKEN}", "id": "{ORG}"}}}}',
    f'"accessToken":"{TOKEN}"',
    f"https://x.my.salesforce.com/sid/{TOKEN}/home",
    f"token 00D000000000003AAA%21AQ8AQsynthetic.value",
    f"sessionid={TOKEN.replace('!', 'x')}",
    f"session_id=abc.def-synthetic",
    f"SessionId%3Dabc.def-synthetic",
    f"{{'accessToken': '{TOKEN}', 'instanceUrl': 'https://x'}}",
    f"{{'refresh_token': 'abc.def-synthetic'}}",
    f"session_id: abc.def-synthetic",
    f"token 00D000000000003AAA%2521AQ8AQsynthetic.value",
])
def test_session_tokens_and_names_are_redacted(text):
    out = redact(text)
    for secret in (TOKEN, "AQ8AQ", "abc.def-synthetic", "synthetic.value"):
        assert secret not in out, out
    assert "REDACTED" in out.upper()


def test_org_and_record_ids_are_not_tokens():
    text = f"org {ORG} user {ADMIN} record 003000000000001AAA!"
    assert redact(text) == text
