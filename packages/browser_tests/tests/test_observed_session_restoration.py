"""Identity restoration regression based on an observed failed Login As attempt."""
import asyncio
import sys
import types
from unittest.mock import AsyncMock

import pytest

from jsc_browser_tests import auth, matrix, runner

ADMIN = "005000000000001AAA"
USER = "005000000000002AAA"
OTHER = "005000000000003AAA"
INSTANCE = "https://synthetic.invalid"


def test_baseline_still_current_skips_logout_instead_of_false_restore_failure(monkeypatch):
    monkeypatch.setattr(auth, "observe_user_id", AsyncMock(return_value=ADMIN))
    logout = AsyncMock(side_effect=AssertionError("No Login As session exists"))
    monkeypatch.setattr(auth, "logout_as_user", logout)
    result = asyncio.run(auth.restore_original_user(object(), INSTANCE, ADMIN))
    assert result == {"status": "OBSERVED", "user_id": ADMIN, "action": "ALREADY_BASELINE"}
    logout.assert_not_awaited()


def test_changed_identity_requires_logout_and_fresh_baseline_observation(monkeypatch):
    observe = AsyncMock(side_effect=[USER, ADMIN])
    logout = AsyncMock()
    monkeypatch.setattr(auth, "observe_user_id", observe)
    monkeypatch.setattr(auth, "logout_as_user", logout)
    result = asyncio.run(auth.restore_original_user(object(), INSTANCE, ADMIN))
    assert result["action"] == "LOGOUT_AS" and result["user_id"] == ADMIN
    assert observe.await_count == 2
    logout.assert_awaited_once()


def test_unknown_identity_does_not_assume_baseline_but_can_restore(monkeypatch):
    monkeypatch.setattr(auth, "observe_user_id", AsyncMock(side_effect=[auth.AuthError("unknown"), ADMIN]))
    logout = AsyncMock()
    monkeypatch.setattr(auth, "logout_as_user", logout)
    result = asyncio.run(auth.restore_original_user(object(), INSTANCE, ADMIN))
    assert result["status"] == "OBSERVED" and result["initial_observation_error"]
    logout.assert_awaited_once()


@pytest.mark.parametrize("after", [USER, OTHER, auth.AuthError("unknown")])
def test_successful_logout_action_without_observed_baseline_is_failure(monkeypatch, after):
    monkeypatch.setattr(auth, "observe_user_id", AsyncMock(side_effect=[USER, after]))
    monkeypatch.setattr(auth, "logout_as_user", AsyncMock())
    with pytest.raises(auth.AuthError):
        asyncio.run(auth.restore_original_user(object(), INSTANCE, ADMIN))


def test_action_timeout_with_verified_baseline_preserves_error_as_warning(monkeypatch):
    secret = "frontdoor.jsp?sid=synthetic-secret"
    monkeypatch.setattr(auth, "observe_user_id", AsyncMock(side_effect=[USER, ADMIN]))
    monkeypatch.setattr(auth, "logout_as_user", AsyncMock(side_effect=RuntimeError(secret)))
    result = asyncio.run(auth.restore_original_user(object(), INSTANCE, ADMIN))
    assert result["status"] == "OBSERVED" and result["action_warning"]
    assert "synthetic-secret" not in str(result)


def test_unverified_identity_and_failed_logout_remain_failure(monkeypatch):
    monkeypatch.setattr(auth, "observe_user_id", AsyncMock(side_effect=[auth.AuthError("before unknown"), auth.AuthError("after unknown")]))
    monkeypatch.setattr(auth, "logout_as_user", AsyncMock(side_effect=auth.AuthError("no logout action")))
    with pytest.raises(auth.AuthError, match="no logout action.*after unknown"):
        asyncio.run(auth.restore_original_user(object(), INSTANCE, ADMIN))


def test_runner_preserves_login_mismatch_without_executing_flow_or_false_logout(monkeypatch, tmp_path):
    class CM:
        async def __aenter__(self): return object()
        async def __aexit__(self, *args): pass
    api = types.ModuleType("playwright.async_api")
    api.async_playwright = lambda: CM()
    monkeypatch.setitem(sys.modules, "playwright.async_api", api)
    page = types.SimpleNamespace(wait_for_selector=AsyncMock())
    monkeypatch.setattr(auth, "get_admin_auth", lambda *_: types.SimpleNamespace(instance_url=INSTANCE, org_id_18="00D000000000001AAA"))
    monkeypatch.setattr(auth, "open_session", AsyncMock(return_value=types.SimpleNamespace(page=page)))
    monkeypatch.setattr(auth, "close_session", AsyncMock())
    monkeypatch.setattr(auth, "login_as_user", AsyncMock())
    monkeypatch.setattr(auth, "observe_user_id", AsyncMock(side_effect=[ADMIN, ADMIN, ADMIN]))
    logout = AsyncMock(side_effect=AssertionError("No impersonation session exists"))
    monkeypatch.setattr(auth, "logout_as_user", logout)
    flow = types.SimpleNamespace(name="synthetic", run=AsyncMock(side_effect=AssertionError("No business flow should execute")))
    result = asyncio.run(runner.run_flow_variation(flow, runner.Variation("test", "standard"), "synthetic", tmp_path, test_user={"user_id": USER}, sf_client=object()))
    assert result.overall_status == "INCOMPLETE" and result.steps == []
    assert result.side_effects["browser_identity"]["status"] == "MISMATCH"
    assert result.side_effects["session_restore"]["action"] == "ALREADY_BASELINE"
    assert not result.side_effects.get("stop_suite")
    logout.assert_not_awaited()
    flow.run.assert_not_awaited()


def test_preflight_retains_original_login_error_when_restoration_also_fails(monkeypatch):
    sf = types.SimpleNamespace(org_display=lambda: {"instanceUrl": INSTANCE, "id": "00D000000000001AAA"})
    monkeypatch.setattr(matrix, "observe_user_id", AsyncMock(return_value=ADMIN))
    monkeypatch.setattr(matrix, "login_as_user", AsyncMock(side_effect=auth.AuthError("login action unavailable")))
    monkeypatch.setattr(matrix, "restore_original_user", AsyncMock(side_effect=auth.AuthError("restoration unknown")))
    result = asyncio.run(matrix.login_as_preflight(object(), {"users": {"standard": {"user_id": USER}}}, sf))
    assert result["standard"] == "FAIL"
    assert "login action unavailable" in result["preflight_error"] and "restoration unknown" in result["preflight_error"]


def test_preflight_failed_login_with_baseline_present_remains_login_failure(monkeypatch):
    sf = types.SimpleNamespace(org_display=lambda: {"instanceUrl": INSTANCE, "id": "00D000000000001AAA"})
    monkeypatch.setattr(matrix, "observe_user_id", AsyncMock(side_effect=[ADMIN, ADMIN]))
    monkeypatch.setattr(matrix, "login_as_user", AsyncMock())
    monkeypatch.setattr(matrix, "restore_original_user", AsyncMock(return_value={"status": "OBSERVED", "user_id": ADMIN, "action": "ALREADY_BASELINE"}))
    result = asyncio.run(matrix.login_as_preflight(object(), {"users": {"standard": {"user_id": USER}}}, sf))
    assert result["standard"] == "FAIL"
    assert "requested browser user" in result["preflight_error"]
    assert "restoration failed" not in result["preflight_error"].lower()
