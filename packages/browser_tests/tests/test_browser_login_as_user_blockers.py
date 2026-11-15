"""`torque browser login --as-user`: a user an admin cannot log in as from the user's
Setup page (inactive, frozen, API only, an Experience Cloud or portal user, the site guest)
is refused before anything is sent to the org, with what is wrong and what to do. What
the org does not answer is not a refusal; the Log in as policy is named in the steps."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from login_helpers import Command, FakeCli  # noqa: E402

from jsc_browser_tests import login  # noqa: E402
from jsc_browser_tests.sf_client import SfClient, SfError  # noqa: E402


def user(**facts):
    return {"user_id": "005000000000002AAA", "username": "test.user@acme.example", "is_active": True,
            "user_type": "Standard", **facts}


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(login, "SfClient", FakeCli)
    monkeypatch.setattr(login.auth, "in_connected_mode", lambda: False)
    monkeypatch.setattr(login.auth, "in_build_only_mode", lambda: False, raising=False)
    monkeypatch.setenv("TORQUE_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("TORQUE_TEST_USERS", raising=False)


@pytest.mark.parametrize("facts, words", [
    ({"is_active": False}, ("inactive", "activate the user")),
    ({"frozen": True}, ("frozen", "unfreezes")),
    ({"api_only": True}, ("API Only User permission", "separate user")),
    ({"user_type": "PowerCustomerSuccess"}, ("Experience Cloud or portal user", "Log in to Experience as User")),
    ({"user_type": "CspLitePortal"}, ("Experience Cloud or portal user", "contact")),
    ({"user_type": "Guest"}, ("guest user", "without signing in")),
], ids=["inactive", "frozen", "api-only", "community", "portal", "guest"])
def test_a_user_who_cannot_be_logged_in_as_is_refused_with_what_to_do(facts, words):
    FakeCli.reset(**{"acme-sbx": {"users": {"test.user@acme.example": user(**facts)}}})
    run = Command("--target-org", "acme-sbx", "--as-user", "test.user@acme.example", "--json")
    assert run.done() == login.EXIT_SIGN_IN and FakeCli.mints() == 0
    reason = run.events()[-1]["reason"]
    assert all(word in reason for word in words) and "Nothing was sent" in reason


def test_facts_the_org_does_not_answer_do_not_refuse():
    FakeCli.reset(**{"acme-sbx": {"users": {"test.user@acme.example": user(frozen=None, api_only=None)}}})
    run = Command("--target-org", "acme-sbx", "--as-user", "test.user@acme.example", "--json")
    assert run.get()[0] == 302 and run.done() == login.EXIT_OK
    steps = " ".join(run.events()[-1]["next_steps"])
    assert "cannot read that policy" in steps and "no Login button" in steps


def test_the_cli_client_reads_frozen_and_api_only_and_tolerates_a_refused_query(monkeypatch):
    asked = []

    def query(self, soql, all_rows=False):
        asked.append(soql)
        if "UserLogin" in soql:
            return [{"IsFrozen": True}]
        raise SfError("sf data query exit 1 (INVALID_FIELD): synthetic")
    monkeypatch.setattr(SfClient, "query", query)
    facts = SfClient("acme-sbx").login_as_facts("005000000000002AAA")
    assert facts == {"frozen": True, "api_only": None}
    assert asked == ["SELECT IsFrozen FROM UserLogin WHERE UserId = '005000000000002AAA'",
                     "SELECT Id FROM PermissionSetAssignment WHERE AssigneeId = '005000000000002AAA' "
                     "AND PermissionSet.PermissionsApiUserOnly = true LIMIT 1"]
