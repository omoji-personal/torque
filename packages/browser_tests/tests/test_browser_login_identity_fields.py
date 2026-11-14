"""`torque browser login` prints the org and user the Salesforce CLI names. A field that
does not look like what it should be (a username, a 15 or 18 character Id, an edition
name) is printed as "(unreadable)", in the JSON event and in the text, and never an
address. The loopback address the command prints on purpose is unchanged.

Offline: the CLI is a double that returns deliberately malformed fields. Genuine CLI
output is not known to do this; the check is defensive."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from login_helpers import ADDRESS, MARKER, ORG, Command, FakeCli  # noqa: E402

from jsc_browser_tests import login  # noqa: E402

TOKEN_SHAPED = f"00D000000000001!AQ8AQ{MARKER}"


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    FakeCli.reset()
    monkeypatch.setattr(login, "SfClient", FakeCli)
    monkeypatch.setattr(login.auth, "in_connected_mode", lambda: False)
    monkeypatch.setenv("TORQUE_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("TORQUE_TEST_USERS", raising=False)


def signed_in(as_json: bool, **org):
    FakeCli.reset(**{"acme-sbx": org})
    run = Command("--target-org", "acme-sbx", *(["--json"] if as_json else []))
    assert run.get()[0] == 302
    assert run.done() == login.EXIT_OK
    assert MARKER not in run.everything()
    return run


@pytest.mark.parametrize("username", [ADDRESS, TOKEN_SHAPED, f"user@acme.example/{MARKER}", f"{MARKER} @x.y"])
def test_a_username_that_is_not_one_is_printed_as_unreadable(username):
    event = signed_in(True, username=username).events()[-1]
    assert event["event"] == "sent" and event["username"] == login.UNREADABLE
    assert event["user_id"] is None, "a username that is not one is not looked up"
    assert not any(call[0] == "user" for call in FakeCli.calls)
    text = signed_in(False, username=username).out.getvalue().splitlines()[1]
    assert f"user {login.UNREADABLE}" in text


def test_a_user_id_and_an_edition_of_the_wrong_shape_are_unreadable():
    run = signed_in(True, user_lookup_id=ADDRESS, organization_type=ADDRESS)
    event = run.events()[-1]
    assert event["user_id"] == login.UNREADABLE and event["org_edition"] == login.UNREADABLE
    assert event["org_id"] == ORG and event["username"] == "admin@acme.example"


def test_ordinary_fields_are_printed_as_they_are():
    event = signed_in(True, organization_type="Developer Edition").events()[-1]
    assert (event["username"], event["user_id"], event["org_edition"]) == \
        ("admin@acme.example", "005000000000001AAA", "Developer Edition")


def test_the_local_address_is_printed_unchanged():
    FakeCli.reset()
    run = Command("--target-org", "acme-sbx", "--json")
    assert run.events()[0]["address"] == run.address and run.address.startswith("http://127.0.0.1:")
    run.get()
    run.done()


def test_a_refusal_names_no_malformed_username():
    message = login.wrong_user("acme-sbx-std", ADDRESS, "std.user@acme.example")
    assert MARKER not in message and login.UNREADABLE in message
    message = login.wrong_user("acme-sbx-std", "std.user@acme.example", TOKEN_SHAPED)
    assert MARKER not in message and login.UNREADABLE in message


@pytest.mark.parametrize("as_json", [True, False])
def test_every_printed_refusal_passes_the_redaction(as_json, capsys):
    output = login.Output(as_json)
    output.fail(login.LoginError(login.EXIT_SIGN_IN, f"the sign-in request failed at {ADDRESS}"))
    printed = capsys.readouterr()
    assert MARKER not in printed.out + printed.err


def test_as_user_with_a_malformed_lookup_answer_says_so_and_signs_nothing_in():
    users = {"std.user@acme.example": {"user_id": f"005/{MARKER}", "username": ADDRESS, "is_active": True},
             "old.user@acme.example": {"user_id": "005000000000003AAA", "username": ADDRESS, "is_active": False}}
    FakeCli.reset(**{"acme-sbx": {"users": users}})
    run = Command("--target-org", "acme-sbx", "--as-user", "std.user@acme.example", "--json")
    assert run.done() == login.EXIT_SIGN_IN and FakeCli.mints() == 0
    assert "unreadable User Id" in run.events()[-1]["reason"] and MARKER not in run.everything()
    run = Command("--target-org", "acme-sbx", "--as-user", "old.user@acme.example", "--json")
    assert run.done() == login.EXIT_SIGN_IN and "inactive" in run.events()[-1]["reason"]
    assert MARKER not in run.everything()
