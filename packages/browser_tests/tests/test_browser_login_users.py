"""`torque browser login` as another user (offline, the Salesforce CLI is a double).

--as-alias: an alias the CLI is separately authorized for as the test user; it must be
the same org as --target-org. --as-user: the admin's tab lands on that user's page in
Setup and the command prints the next steps."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from login_helpers import ADDRESS, MARKER, ORG, OTHER_ORG, Command, FakeCli  # noqa: E402

from jsc_browser_tests import auth, cli, login  # noqa: E402
from jsc_browser_tests.sf_client import FakeSfClient, SfClient, SfError  # noqa: E402

STD = {"user_id": "005000000000002AAA", "username": "std.user@acme.example", "is_active": True}
USERS = {STD["username"]: STD, STD["user_id"]: STD,
         "old.user@acme.example": {"user_id": "005000000000003AAA", "username": "old.user@acme.example",
                                   "is_active": False}}


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    FakeCli.reset(**{"acme-sbx": {"users": USERS}, "acme-sbx-std": {"username": STD["username"]},
                     "acme-uat-std": {"org_id": OTHER_ORG, "username": STD["username"]}})
    monkeypatch.setattr(login, "SfClient", FakeCli)
    monkeypatch.setattr(login.auth, "in_connected_mode", lambda: False)
    (tmp_path / "client" / "config").mkdir(parents=True)
    monkeypatch.setenv("TORQUE_WORKSPACE", str(tmp_path / "client"))
    monkeypatch.delenv("TORQUE_TEST_USERS", raising=False)


def test_as_alias_signs_in_as_that_alias_after_checking_it_is_the_same_org():
    run = Command("--target-org", "acme-sbx", "--as-alias", "acme-sbx-std", "--json")
    assert ("identity", "acme-sbx") in FakeCli.calls and ("identity", "acme-sbx-std") in FakeCli.calls
    assert run.get()[0] == 302
    assert run.done() == login.EXIT_OK
    assert [call for call in FakeCli.calls if call[0] == "mint"] == [("mint", "acme-sbx-std", None, None)]
    redirected = run.events()[1]
    assert redirected["as_alias"] == "acme-sbx-std" and redirected["username"] == STD["username"]
    assert redirected["org_id"] == ORG


def test_as_alias_names_the_user_in_plain_text():
    run = Command("--target-org", "acme-sbx", "--as-alias", "acme-sbx-std")
    run.get()
    run.done()
    line = run.out.getvalue().splitlines()[1]
    assert f"user {STD['username']}" in line and "Sent through alias acme-sbx-std." in line


def test_as_alias_for_another_org_is_refused_before_any_address():
    run = Command("--target-org", "acme-sbx", "--as-alias", "acme-uat-std", wait=False)
    assert run.done() == login.EXIT_ORG_MISMATCH
    text = run.err.getvalue()
    assert ORG in text and OTHER_ORG in text and "Nothing was signed in" in text
    assert run.out.getvalue() == "" and FakeCli.mints() == 0


def test_as_alias_that_is_not_authorized_names_that_alias():
    run = Command("--target-org", "acme-sbx", "--as-alias", "missing-alias", wait=False)
    assert run.done() == login.EXIT_NOT_AUTHORIZED
    assert "sf org login web --alias missing-alias" in run.err.getvalue()


@pytest.mark.parametrize("value", [STD["username"], STD["user_id"], "  " + STD["username"] + " "])
def test_as_user_lands_the_admin_tab_on_the_users_setup_page_and_says_what_to_do(value):
    run = Command("--target-org", "acme-sbx", "--as-user", value, "--json")
    assert run.get()[0] == 302
    assert run.done() == login.EXIT_OK
    setup = auth.user_setup_path(STD["user_id"])
    assert setup == "/lightning/setup/ManageUsers/page?address=%2F005000000000002AAA%3Fnoredirect%3D1"
    assert ("mint", "acme-sbx", setup, None) in FakeCli.calls, "the admin alias signs in, on the user's Setup page"
    redirected = run.events()[1]
    assert redirected["login_as"] == {"user_id": STD["user_id"], "username": STD["username"], "route": "setup-page"}
    assert redirected["username"] == "admin@acme.example", "the tab is the admin until Login is clicked"
    steps = " ".join(redirected["next_steps"])
    for words in ("Click Login", f'"Logged in as {STD["username"]}" banner', 'do not click "Log out as"',
                  "close the tab", "Administrators Can Log in as Any User", "API Only User", setup):
        assert words in steps
    assert MARKER not in run.everything()


def test_as_user_steps_are_printed_as_numbered_lines():
    run = Command("--target-org", "acme-sbx", "--as-user", STD["username"])
    run.get()
    run.done()
    text = run.out.getvalue()
    assert "Next steps:\n1. The tab was sent, as the admin, to the page of" in text and "\n2. Click Login on that page." in text


def test_as_user_direct_uses_the_existing_login_as_address_and_is_labelled_unproven():
    run = Command("--target-org", "acme-sbx", "--as-user", STD["username"], "--direct", "--json")
    run.get()
    assert run.done() == login.EXIT_OK
    direct = auth.login_as_path(ORG, STD["user_id"])
    assert direct == f"/servlet/servlet.su?oid={ORG}&suorgadminid=005000000000002&retURL=%2F&targetURL=%2F"
    assert ("mint", "acme-sbx", direct, None) in FakeCli.calls
    redirected = run.events()[1]
    assert redirected["login_as"]["route"] == "direct" and "unproven" in redirected["next_steps"][0]
    assert auth.user_setup_path(STD["user_id"]) in redirected["next_steps"][1], "the proven route is the fallback"


@pytest.mark.parametrize("value,words", [("nobody@acme.example", "no user"),
                                         ("old.user@acme.example", "inactive")])
def test_as_user_that_cannot_be_used(value, words):
    run = Command("--target-org", "acme-sbx", "--as-user", value, wait=False)
    assert run.done() == login.EXIT_SIGN_IN
    assert words in run.err.getvalue() and run.out.getvalue() == "" and FakeCli.mints() == 0


@pytest.mark.parametrize("argv", [
    ("--as-alias", "acme-sbx-std", "--as-user", STD["username"]),
    ("--direct",),
    ("--as-user", STD["username"], "--path", "/lightning/page/home"),
    ("--as-user", STD["username"], "--storage-state", "state.json"),
    ("--as-user", " "),
    ("--as-alias", "-o"),
])
def test_user_options_that_do_not_fit_together(argv):
    try:
        run = Command("--target-org", "acme-sbx", *argv, wait=False)
    except SystemExit as stop:
        assert stop.code == login.EXIT_USAGE
        return
    assert run.done() == login.EXIT_USAGE and FakeCli.calls == []


def test_help_says_api_only_users_cannot_be_signed_in(capsys):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["login", "--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "API Only User permission cannot be signed in to a browser" in text
    assert "--as-alias USER_ALIAS" in text and "--as-user USERNAME_OR_ID" in text and "unproven" in text
    assert "exit codes:" in text


def test_find_user_queries_by_id_or_username_with_the_value_escaped(monkeypatch):
    asked = []

    def query(self, soql, all_rows=False):
        asked.append(soql)
        return [{"Id": STD["user_id"], "Username": STD["username"], "IsActive": True}]
    monkeypatch.setattr(SfClient, "query", query)
    client = SfClient("acme-sbx")
    assert client.find_user(STD["user_id"]) == STD
    assert client.find_user("o'brien@acme.example")["user_id"] == STD["user_id"]
    assert asked == [f"SELECT Id, Username, IsActive FROM User WHERE Id = '{STD['user_id']}'",
                     "SELECT Id, Username, IsActive FROM User WHERE Username = 'o\\'brien@acme.example'"]
    assert FakeSfClient().find_user("nobody@acme.example") is None


def test_a_failed_user_lookup_is_reported_without_the_cli_text(monkeypatch):
    def broken(self, value):
        raise SfError(f"sf data query exit 1: {ADDRESS}", "other")
    monkeypatch.setattr(FakeCli, "find_user", broken)
    run = Command("--target-org", "acme-sbx", "--as-user", STD["username"], wait=False)
    assert run.done() == login.EXIT_SIGN_IN
    assert "the user lookup failed" in run.err.getvalue() and MARKER not in run.everything()


# A role of the client's test-user seed that names its own alias (cli_alias).

def _seed_with_role(tmp_path, username=STD["username"], org=ORG):
    import json
    (tmp_path / "client" / "config" / "test-users.json").write_text(json.dumps({
        "schema_version": 1, "org_id_18": org, "alias": "acme-sbx",
        "users": {"standard": {"username": username, "user_id": STD["user_id"], "cli_alias": "acme-sbx-std"},
                  "manager": {"username": "manager@acme.example", "user_id": "005000000000004AAA"}}}),
        encoding="utf-8")


def test_a_seed_role_with_a_cli_alias_signs_in_through_that_alias(tmp_path):
    _seed_with_role(tmp_path)
    run = Command("--target-org", "acme-sbx", "--as-alias", "standard", "--json")
    assert run.get()[0] == 302
    assert run.done() == login.EXIT_OK
    assert ("mint", "acme-sbx-std", None, None) in FakeCli.calls
    redirected = run.events()[1]
    assert redirected["role"] == "standard" and redirected["as_alias"] == "acme-sbx-std"
    assert redirected["username"] == STD["username"]
    assert redirected["org_check"] == "org matches this client's test-user seed"


def test_another_user_than_the_seed_records_for_the_role_is_not_signed_in(tmp_path):
    _seed_with_role(tmp_path, username="someone.else@acme.example")
    run = Command("--target-org", "acme-sbx", "--as-alias", "standard", "--json")
    status, headers, body = run.get()
    assert status == 502 and "Location" not in headers and "nothing was signed in" in body
    assert run.done() == login.EXIT_ORG_MISMATCH
    assert "the test-user seed records someone.else@acme.example" in run.events()[-1]["reason"]


def test_a_role_without_a_cli_alias_is_taken_as_an_alias_name(tmp_path):
    _seed_with_role(tmp_path)
    run = Command("--target-org", "acme-sbx", "--as-alias", "manager", wait=False)
    assert run.done() == login.EXIT_NOT_AUTHORIZED, "there is no alias called manager"
    assert "sf org login web --alias manager" in run.err.getvalue()
