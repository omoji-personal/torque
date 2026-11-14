"""After the redirect, all `torque browser login` knows is that the tab was sent to the
org's sign-in address as a user. It cannot see the tab (one live run landed on a
"Change Your Password" page while the command succeeded), so its output never says
"signed in" and tells the agent what to check in the page and what each page means."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from login_helpers import Command, FakeCli  # noqa: E402

from jsc_browser_tests import cli, login  # noqa: E402

USERS = {"std.user@acme.example": {"user_id": "005000000000002AAA", "username": "std.user@acme.example",
                                   "is_active": True}}


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    FakeCli.reset(**{"acme-sbx": {"users": USERS}})
    monkeypatch.setattr(login, "SfClient", FakeCli)
    monkeypatch.setattr(login.auth, "in_connected_mode", lambda: False)
    monkeypatch.setenv("TORQUE_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("TORQUE_TEST_USERS", raising=False)


@pytest.mark.parametrize("extra", [(), ("--as-user", "std.user@acme.example")], ids=["admin", "as-user"])
@pytest.mark.parametrize("as_json", [True, False], ids=["json", "text"])
def test_a_sent_tab_is_never_called_signed_in(extra, as_json):
    run = Command("--target-org", "acme-sbx", *extra, *(["--json"] if as_json else []))
    assert run.get()[0] == 302 and run.done() == login.EXIT_OK
    printed = run.everything().casefold().replace("cannot be signed in", "")   # the API-only note
    assert "signed in" not in printed
    if as_json:
        sent = run.events()[-1]
        assert sent["event"] == "sent" and sent["session_verified"] is False
        advice = sent["check_in_page"]
    else:
        assert "Not confirmed" in run.out.getvalue()
        advice = run.err.getvalue()
    for words in ("check which user the page shows", "A login page", "Change Your Password", "consultant"):
        assert words in advice


def test_the_help_says_what_exit_0_means():
    help_text = cli.build_parser()._subparsers._group_actions[0].choices["login"].format_help()
    assert "cannot see the tab" in help_text and "not yet confirmed" in " ".join(help_text.split())
