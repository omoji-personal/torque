"""Offline regressions for identity, coverage, cleanup and credential boundaries."""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import socket
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from jsc_browser_tests import auth, cli, runner, report, suite
from jsc_browser_tests.cell_runner import run_cell
from jsc_browser_tests.flow_spec import FlowSpec
from jsc_browser_tests.sf_client import FakeSfClient

ADMIN = "005000000000001AAA"
STANDARD = "005000000000002AAA"
SID = "SYNTHETIC_SESSION_MUST_NOT_APPEAR"
SECRET_ERROR = f"Page.goto: timed out navigating https://example.invalid/secur/frontdoor.jsp?sid={SID}&retURL=/"


@pytest.fixture(autouse=True)
def prohibit_live_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline regression attempted external I/O")

    real_connect = socket.socket.connect

    def guarded_connect(self, address, *a, **kw):
        # socket.socketpair() is a true AF_UNIX syscall on POSIX, never going
        # through socket.connect(). On Windows it has no AF_UNIX equivalent,
        # so Python emulates it with a real loopback TCP connection - and
        # asyncio's own internal wakeup self-pipe (created even for pure
        # async/await code doing no real I/O) uses socketpair(). Block real
        # external hosts; let Python's own loopback plumbing through.
        host = address[0] if isinstance(address, tuple) else None
        if host in ("127.0.0.1", "::1", "localhost"):
            return real_connect(self, address, *a, **kw)
        raise AssertionError("Offline regression attempted external I/O")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


class Flow(runner.BaseFlow):
    name = "synthetic"
    spec = FlowSpec(name=name, workflow="E1", writes=False,
                    profiles=["admin", "standard"], variations=[runner.Variation("happy")])

    def __init__(self, steps=None):
        self.steps = [] if steps is None else steps
        self.provisioned = self.executed = self.torn_down = False

    async def provision(self, ctx):
        self.provisioned = True
        return {}

    async def run(self, page, ctx, variation):
        self.executed = True
        return self.steps

    async def teardown(self, ctx):
        self.torn_down = True


def context(tmp_path):
    return types.SimpleNamespace(profile="admin", target_org="synthetic-org", page=None,
                                 variation=runner.Variation("happy"), sf=FakeSfClient(),
                                 run_dir=tmp_path, runid="TEST-one")


def passed():
    return [runner.StepResult("observed", "PASS", "USER_FIDELITY")]


@pytest.mark.parametrize("steps", [[], [runner.StepResult("skip", "SKIP", "USER_FIDELITY")],
    [runner.StepResult("backend", "PASS", "BACKEND_DIAGNOSTIC")],
    passed() + [runner.StepResult("backend", "PASS", "BACKEND_DIAGNOSTIC")]])
def test_unobserved_or_partial_browser_cells_never_pass(tmp_path, steps):
    flow = Flow(steps)
    cell = asyncio.run(run_cell(flow, context(tmp_path)))
    assert cell.overall_status in {"INCOMPLETE", "SKIP"}
    assert report.exit_code(report.score_run([cell]), True, False) != 0
    assert flow.torn_down


def test_browser_steps_with_failed_teardown_fail(tmp_path):
    class BrokenCleanup(Flow):
        async def teardown(self, ctx):
            raise RuntimeError(SECRET_ERROR)
    cell = asyncio.run(run_cell(BrokenCleanup(passed()), context(tmp_path)))
    assert cell.overall_status == "FAIL"
    assert SID not in json.dumps(cell.side_effects)
    assert "teardown_error" in cell.side_effects


@pytest.mark.parametrize("crud,happy", [(False, False), (True, True)])
def test_failed_negative_and_crud_assertions_cannot_exit_zero(crud, happy):
    cell = runner.FlowResult("synthetic", "admin", "synthetic-org", "FAIL",
                             side_effects={"is_crud": crud, "is_happy": happy})
    score = report.score_run([cell])
    assert score["score"] == 95
    assert report.exit_code(score, True, False) == 2


def stub_browser(monkeypatch, observed_users, *, logout_error=None):
    class AsyncCM:
        async def __aenter__(self): return object()
        async def __aexit__(self, *args): pass
    fake_api = types.ModuleType("playwright.async_api")
    fake_api.async_playwright = lambda: AsyncCM()
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_api)
    page = types.SimpleNamespace(wait_for_selector=AsyncMock())
    session = types.SimpleNamespace(page=page, context=object(), browser=object())
    monkeypatch.setattr(auth, "get_admin_auth", lambda *_: types.SimpleNamespace(
        instance_url="https://example.invalid", org_id_18="00D000000000001AAA"))
    monkeypatch.setattr(auth, "open_session", AsyncMock(return_value=session))
    monkeypatch.setattr(auth, "close_session", AsyncMock())
    monkeypatch.setattr(auth, "observe_user_id", AsyncMock(side_effect=observed_users))
    monkeypatch.setattr(auth, "login_as_user", AsyncMock())
    monkeypatch.setattr(auth, "logout_as_user", AsyncMock(side_effect=logout_error))


def run_standard(flow, tmp_path, user=None):
    return asyncio.run(runner.run_flow_variation(
        flow, runner.Variation("happy", profile="standard"), "synthetic-org", tmp_path,
        test_user=user, sf_client=object()))


def test_missing_nonadmin_user_cannot_fall_back_to_admin(tmp_path, monkeypatch):
    def no_auth(*args): raise AssertionError("Must not open an admin session")
    monkeypatch.setattr(auth, "get_admin_auth", no_auth)
    flow = Flow(passed())
    result = run_standard(flow, tmp_path)
    assert result.overall_status == "INCOMPLETE"
    assert not flow.provisioned and not flow.executed


def test_login_as_identity_mismatch_never_provisions(tmp_path, monkeypatch):
    stub_browser(monkeypatch, [ADMIN, ADMIN, ADMIN])
    flow = Flow(passed())
    result = run_standard(flow, tmp_path, {"user_id": STANDARD})
    assert result.overall_status == "INCOMPLETE"
    assert not flow.provisioned and not flow.executed
    assert result.side_effects["browser_identity"]["status"] == "MISMATCH"
    assert result.side_effects["session_restore"]["user_id"] == ADMIN


def test_unavailable_browser_identity_is_not_assumed(tmp_path, monkeypatch):
    stub_browser(monkeypatch, [auth.AuthError("not available")])
    flow = Flow(passed())
    result = run_standard(flow, tmp_path, {"user_id": STANDARD})
    assert result.overall_status == "INCOMPLETE"
    assert not flow.executed
    assert result.side_effects["browser_identity"]["status"] == "NOT_CHECKED"


@pytest.mark.parametrize("observations,logout_error", [([ADMIN, STANDARD], RuntimeError(SECRET_ERROR)),
                                                        ([ADMIN, STANDARD, STANDARD], None)])
def test_failed_or_incorrect_logout_is_reported_and_stops_suite(tmp_path, monkeypatch, observations, logout_error):
    stub_browser(monkeypatch, observations, logout_error=logout_error)
    flow = Flow(passed())
    result = run_standard(flow, tmp_path, {"user_id": STANDARD})
    assert flow.executed
    assert result.overall_status == "FAIL"
    assert result.side_effects["stop_suite"] is True
    assert "session_restore_error" in result.side_effects
    assert SID not in json.dumps(result.side_effects)


def test_observed_user_and_restoration_allow_success(tmp_path, monkeypatch):
    stub_browser(monkeypatch, [ADMIN, STANDARD, ADMIN])
    result = run_standard(Flow(passed()), tmp_path, {"user_id": STANDARD})
    assert result.overall_status == "PASS"
    assert result.side_effects["browser_identity"]["observed_user_id"] == STANDARD
    assert result.side_effects["session_restore"]["status"] == "OBSERVED"


def test_current_user_observer_validates_actual_page_value():
    page = types.SimpleNamespace(evaluate=AsyncMock(return_value=STANDARD), wait_for_function=AsyncMock())
    assert asyncio.run(auth.observe_user_id(page)) == STANDARD
    page.evaluate = AsyncMock(return_value=None)
    with pytest.raises(auth.AuthError):
        asyncio.run(auth.observe_user_id(page))


def test_frontdoor_error_does_not_expose_url_and_closes_owned_browser():
    page = types.SimpleNamespace(goto=AsyncMock(side_effect=RuntimeError(SECRET_ERROR)))
    context = types.SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
    browser = types.SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
    pw = types.SimpleNamespace(chromium=types.SimpleNamespace(launch=AsyncMock(return_value=browser)))
    admin = auth.AdminAuth("synthetic", "00D000000000001AAA", "https://example.invalid", SECRET_ERROR)
    with pytest.raises(auth.AuthError) as caught:
        asyncio.run(auth.open_session(pw, admin))
    assert SID not in str(caught.value) and "frontdoor.jsp" not in str(caught.value)
    assert caught.value.__suppress_context__
    context.close.assert_awaited_once()
    browser.close.assert_awaited_once()


@pytest.mark.parametrize("raise_error", [True, False])
def test_preflight_failure_never_executes_cells(tmp_path, raise_error):
    async def preflight(config):
        if raise_error: raise RuntimeError(SECRET_ERROR)
        return {"standard": "FAIL"}
    execute = AsyncMock()
    path = tmp_path / "manifest.json"
    code = asyncio.run(suite.run_suite({"sf": FakeSfClient(), "target_org": "synthetic",
        "flows": [Flow(passed())], "profiles": ["admin", "standard"],
        "run_dir": str(tmp_path), "manifest_path": str(path), "audit_log": str(tmp_path / "audit"),
        "preflight": preflight, "cell_executor": execute}))
    assert code != 0
    execute.assert_not_awaited()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert all(c["status"] == "INCOMPLETE" for c in payload["cells"])
    assert SID not in path.read_text(encoding="utf-8")


def test_live_preflight_missing_seed_does_not_open_browser(monkeypatch):
    def unexpected(*args): raise AssertionError("No browser should open")
    monkeypatch.setattr(auth, "get_admin_auth", unexpected)
    result = asyncio.run(suite._live_preflight({"profiles": ["standard"], "seed": {}}))
    assert result["standard"] == "FAIL"


def test_suite_stops_after_unrestored_session(tmp_path):
    async def execute(cell, config):
        return runner.FlowResult("synthetic", cell.profile, "synthetic", "FAIL",
                                 side_effects={"session_restore_error": "not restored", "stop_suite": True})
    executor = AsyncMock(side_effect=execute)
    code = asyncio.run(suite.run_suite({"sf": FakeSfClient(), "target_org": "synthetic",
        "flows": [Flow(passed())], "profiles": ["admin", "standard"], "run_dir": str(tmp_path),
        "preflight": AsyncMock(return_value={}), "cell_executor": executor}))
    assert code != 0 and executor.await_count == 1


@pytest.mark.parametrize("case", ["empty", "skip", "cleanup", "negative", "crash"])
def test_short_cli_routes_apply_suite_completion_rules(tmp_path, monkeypatch, case):
    monkeypatch.setenv("TORQUE_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(suite, "_resolve_org_lazy", lambda _: types.SimpleNamespace(detected_org_type="sandbox"))
    flow = Flow(passed())
    flow.spec = FlowSpec(name=flow.name, workflow="CRUD" if case == "negative" else "E1",
                         writes=(case == "cleanup"), variations=[] if case == "empty" else [runner.Variation("happy")])
    status = "SKIP" if case == "skip" else "FAIL" if case == "negative" else "PASS"
    execution = AsyncMock(side_effect=RuntimeError(SECRET_ERROR)) if case == "crash" else AsyncMock(
        return_value=runner.FlowResult(flow.name, "admin", "synthetic", status))
    monkeypatch.setattr(runner, "run_flow_variation", execution)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = cli._run_flow_with_profiles(flow, "synthetic", ["admin"])
    assert code != 0
    if case == "cleanup": assert code == 4
    manifests = list(tmp_path.rglob("manifest.json"))
    assert len(manifests) == 1
    assert SID not in manifests[0].read_text(encoding="utf-8") + output.getvalue()


def test_target_org_and_flow_labels_cannot_escape_artifact_directory(tmp_path, monkeypatch):
    client = tmp_path / "client"
    client.mkdir()
    monkeypatch.setenv("TORQUE_WORKSPACE", str(client))
    escaped = tmp_path / "outside"
    flow = Flow(passed())
    flow.name = "../../different-flow"
    execute = AsyncMock(return_value=runner.FlowResult(flow.name, "admin", str(escaped), "PASS"))
    monkeypatch.setattr(runner, "run_flow_variation", execute)
    with contextlib.redirect_stdout(io.StringIO()):
        assert cli._run_flow_with_profiles(flow, str(escaped), ["admin"]) == 0
    assert not escaped.exists()
    assert len(list(client.rglob("manifest.json"))) == 1
    # The label encoding must not change the actual execution target.
    assert execute.call_args.args[2] == str(escaped)


def test_result_serialization_redacts_nested_credentials():
    cell = runner.FlowResult("synthetic", "admin", "synthetic", "FAIL", error=SECRET_ERROR,
        steps=[runner.StepResult("step", "FAIL", "USER_FIDELITY", detail=SECRET_ERROR)],
        side_effects={"raw": {"access_token": SID}, "detail": SECRET_ERROR})
    assert SID not in json.dumps(cli._result_to_dict(cell))


def test_teardown_without_carriers_does_not_report_clear():
    assert cli.cmd_suite_teardown(types.SimpleNamespace(target_org="synthetic", runid="TEST-one")) == 4


def test_suite_preserves_record_prefix_when_encoding_artifact_paths(tmp_path, monkeypatch):
    observed = {}
    async def execute(flow, variation, target_org, run_dir, **kwargs):
        observed.update(runid=kwargs["runid"], artifact_dir=run_dir.name)
        return runner.FlowResult(flow.name, "admin", target_org, "PASS")
    monkeypatch.setattr(runner, "run_flow_variation", execute)
    code = asyncio.run(suite.run_suite({"sf": FakeSfClient(), "target_org": "synthetic",
        "flows": [Flow(passed())], "profiles": ["admin"], "run_dir": str(tmp_path),
        "runid": "TEST-one"}))
    assert code == 0
    assert observed["runid"] == "TEST-one"
    assert observed["artifact_dir"] != "TEST-one"
