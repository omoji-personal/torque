"""`torque browser login`, host-tab mode: the command prints only a local address, the
Salesforce address is asked for when the browser opens it and answered as a redirect,
and it never appears in anything the command prints, logs or raises.

Offline: the Salesforce CLI is a double (login_helpers.FakeCli) or a stub program."""
from __future__ import annotations

import json
import logging
import os
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from login_helpers import ADDRESS, MARKER, NAVIGATION, ORG, OTHER_ORG, Command, FakeCli  # noqa: E402

from jsc_browser_tests import login, relay  # noqa: E402
from jsc_browser_tests.sf_client import SfClient, SfError  # noqa: E402


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    FakeCli.reset()
    monkeypatch.setattr(login, "SfClient", FakeCli)
    monkeypatch.setattr(login.auth, "in_connected_mode", lambda: False)
    client = tmp_path / "client"
    (client / "config").mkdir(parents=True)
    monkeypatch.setenv("TORQUE_WORKSPACE", str(client))
    monkeypatch.delenv("TORQUE_TEST_USERS", raising=False)
    return client


def test_prints_only_the_local_address_then_redirects_the_tab():
    run = Command("--target-org", "acme-sbx")
    first = run.out.getvalue().splitlines()[0]
    assert first == run.address and first.startswith("http://127.0.0.1:") and len(run.code_path) >= 33
    assert FakeCli.mints() == 0, "the Salesforce address is asked for only when the browser opens the local one"
    status, headers, body = run.get()
    assert (status, headers.get("Location"), body) == (302, ADDRESS, "")
    assert headers.get("Cache-Control") == "no-store"
    assert run.done() == login.EXIT_OK
    assert FakeCli.mints() == 1
    lines = run.out.getvalue().splitlines()
    assert len(lines) == 2 and lines[1].startswith(f"Tab sent to the org's sign-in address: org {ORG} "
                                                   "(sandbox, Enterprise Edition), "
                                                   "user admin@acme.example, User Id 005000000000001AAA")
    assert login.NOT_CHECKED in lines[1]
    assert "check which user the page shows" in run.err.getvalue()
    # Seen in a live run: an expired password lands the tab on a password page although the sign-in worked.
    assert "Change Your Password" in run.err.getvalue() and "password has expired" in run.err.getvalue()
    assert MARKER not in run.everything() and "frontdoor" not in run.everything()


def test_json_output_is_two_objects_and_nothing_else():
    run = Command("--target-org", "acme-sbx", "--json", "--timeout", "60")
    assert run.get()[0] == 302
    assert run.done() == login.EXIT_OK
    waiting, redirected = run.events()
    assert set(waiting) == {"event", "address", "timeout_seconds", "expires_at"}
    assert waiting["event"] == "waiting" and waiting["timeout_seconds"] == 60 and waiting["expires_at"].endswith("Z")
    assert redirected == {"event": "sent", "session_verified": False, "org_id": ORG, "org_kind": "sandbox",
                          "org_edition": "Enterprise Edition",
                          "username": "admin@acme.example", "user_id": "005000000000001AAA",
                          "org_check": login.NOT_CHECKED, "check_in_page": login.CHECK_IN_PAGE}
    assert run.err.getvalue() == "" and len(run.out.getvalue().splitlines()) == 2
    assert MARKER not in run.everything()


def test_production_and_unknown_org_kinds_are_named():
    FakeCli.reset(**{"acme-prod": {"is_sandbox": False}, "acme-x": {"is_sandbox": None}})
    for alias, kind in (("acme-prod", "production"), ("acme-x", "unknown")):
        run = Command("--target-org", alias, "--json")
        run.get()
        assert run.done() == 0 and run.events()[1]["org_kind"] == kind


def test_the_address_works_once():
    run = Command("--target-org", "acme-sbx")
    assert run.get()[0] == 302
    run.done()
    with pytest.raises(OSError):
        run.get()
    assert FakeCli.mints() == 1


def test_a_client_that_is_not_a_browser_navigation_gets_nothing():
    run = Command("--target-org", "acme-sbx")
    for headers in ({},                                                                    # curl, a script
                    {"Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty", "Sec-Fetch-Site": "cross-site"},  # fetch()
                    {"Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "image"}):            # an image request
        status, answer, body = run.get(headers=headers)
        assert status == 400 and "Location" not in answer and MARKER not in body
    status, answer, _ = run.get(headers={**NAVIGATION, "Sec-Purpose": "prefetch;prerender"})
    assert status == 503 and "Location" not in answer
    assert run.get(method="POST")[0] == 405 and run.get(method="HEAD")[0] == 405
    assert FakeCli.mints() == 0, "nothing was asked of Salesforce for any of them"
    # A navigation the host started from the page that is open still works.
    assert run.get(headers={**NAVIGATION, "Sec-Fetch-Site": "cross-site"})[0] == 302
    assert run.done() == login.EXIT_OK and FakeCli.mints() == 1


def test_any_client_option_answers_a_browser_without_fetch_metadata():
    run = Command("--target-org", "acme-sbx", "--any-client")
    assert run.get(headers={})[0] == 302
    assert run.done() == login.EXIT_OK


def test_wrong_paths_get_404_and_too_many_close_the_listener():
    run = Command("--target-org", "acme-sbx")
    assert run.get(path="/favicon.ico")[0] == 404
    assert run.get(path="/" + "x" * 32)[0] == 404
    assert run.get()[0] == 302, "the right address still works after wrong ones"
    run.done()
    FakeCli.reset()
    run = Command("--target-org", "acme-sbx", "--json")
    for _ in range(relay.MAX_WRONG_REQUESTS):
        assert run.get(path="/guess")[0] == 404
    assert run.done() == login.EXIT_SIGN_IN
    assert "too many requests" in run.events()[-1]["reason"] and FakeCli.mints() == 0


def test_the_host_header_must_be_the_loopback_address():
    run = Command("--target-org", "acme-sbx")
    status, headers, _ = run.get(host=f"rebound.example:{run.port}")
    assert status == 421 and "Location" not in headers and FakeCli.mints() == 0
    assert run.get(host=f"localhost:{run.port}")[0] == 302
    run.done()


def test_nobody_opens_the_address():
    run = Command("--target-org", "acme-sbx", "--timeout", "1", "--json")
    assert run.done() == login.EXIT_TIMEOUT
    failed = run.events()[-1]
    assert failed["event"] == "failed" and failed["exit_code"] == login.EXIT_TIMEOUT
    assert "within 1 seconds" in failed["reason"] and FakeCli.mints() == 0
    with pytest.raises(OSError):
        run.get()


def test_the_listener_port_cannot_be_shared():
    run = Command("--target-org", "acme-sbx")
    other = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        with pytest.raises(OSError):
            other.bind(("127.0.0.1", run.port))
    finally:
        other.close()
    run.get()
    run.done()


def test_no_local_port(monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError(98, f"address in use near {ADDRESS}")
    monkeypatch.setattr(login.relay, "Relay", refuse)
    run = Command("--target-org", "acme-sbx", wait=False)
    assert run.done() == login.EXIT_BIND
    assert "no local port" in run.err.getvalue() and MARKER not in run.everything()


@pytest.mark.parametrize("kind,code,words", [
    ("not_authorized", login.EXIT_NOT_AUTHORIZED, "sf org login web --alias acme-sbx"),
    ("cli_missing", login.EXIT_CLI_MISSING, "not installed"),
    ("other", login.EXIT_SIGN_IN, "the org identity query failed"),
])
def test_the_alias_cannot_be_used(kind, code, words):
    FakeCli.reset(**{"acme-sbx": {"identity_error": SfError(f"sf data query exit 1: see {ADDRESS}", kind)}})
    run = Command("--target-org", "acme-sbx", wait=False)
    assert run.done() == code
    assert words in run.err.getvalue() and run.out.getvalue() == "", "no address is printed for an unusable alias"
    assert MARKER not in run.everything() and FakeCli.mints() == 0
    if kind == "not_authorized":
        assert "outside the agent" in run.err.getvalue()


def test_an_unknown_alias_is_reported_as_not_authorized():
    run = Command("--target-org", "nobody", wait=False)
    assert run.done() == login.EXIT_NOT_AUTHORIZED


@pytest.mark.parametrize("kind,code,words", [
    ("not_authorized", login.EXIT_NOT_AUTHORIZED, "sf org login web"),
    ("api_only", login.EXIT_SIGN_IN, "API Only User"),
    ("other", login.EXIT_SIGN_IN, "the sign-in request failed"),
])
def test_the_cli_refuses_at_the_moment_of_the_request(kind, code, words):
    FakeCli.reset(**{"acme-sbx": {"mint_error": SfError(f"sf org open exit 1: {ADDRESS}", kind)}})
    run = Command("--target-org", "acme-sbx", "--json")
    status, headers, body = run.get()
    assert status == 502 and "Location" not in headers and MARKER not in body
    assert run.done() == code
    assert words in run.events()[-1]["reason"] and MARKER not in run.everything()
    assert FakeCli.mints() == 1, "one attempt, no retry loop"


def test_an_unexpected_error_while_minting_keeps_only_its_type(monkeypatch):
    def explode(self, target_org=None, *, path=None, api_version=None):
        raise RuntimeError(f"unexpected, with {ADDRESS} inside")
    monkeypatch.setattr(FakeCli, "org_frontdoor", explode)
    run = Command("--target-org", "acme-sbx")
    status, _, body = run.get()
    assert status == 502 and MARKER not in body
    assert run.done() == login.EXIT_SIGN_IN
    assert "RuntimeError" in run.err.getvalue() and MARKER not in run.everything()


def test_the_cli_answers_without_an_address():
    FakeCli.reset(**{"acme-sbx": {"url": ""}})
    run = Command("--target-org", "acme-sbx")
    assert run.get()[0] == 502
    assert run.done() == login.EXIT_SIGN_IN and "no sign-in address" in run.err.getvalue()


def test_an_address_that_is_not_one_header_line_is_not_sent():
    FakeCli.reset(**{"acme-sbx": {"url": ADDRESS + "\r\nSet-Cookie: injected=1"}})
    run = Command("--target-org", "acme-sbx")
    status, headers, body = run.get()
    assert status == 502 and "Location" not in headers and "Set-Cookie" not in headers and MARKER not in body
    assert run.done() == login.EXIT_SIGN_IN


def test_an_unexpected_failure_of_the_command_shows_only_its_type(monkeypatch):
    def broken(alias, org_id):
        raise ValueError(f"a bug that mentions {ADDRESS}")
    monkeypatch.setattr(login, "check_against_client_record", broken)
    run = Command("--target-org", "acme-sbx", "--json", wait=False)
    assert run.done() == login.EXIT_SIGN_IN
    assert run.events()[-1]["reason"].startswith("unexpected failure (ValueError)") and MARKER not in run.everything()


def test_alias_pointed_elsewhere_between_start_and_request_is_not_signed_in():
    FakeCli.reset(**{"acme-sbx": {"mint_org_id": OTHER_ORG}})
    run = Command("--target-org", "acme-sbx")
    status, headers, body = run.get()
    assert status == 502 and "Location" not in headers and "nothing was signed in" in body
    assert run.done() == login.EXIT_ORG_MISMATCH


# The client's own records (A3): compared when they name an org ID, said when they do not.

def _seed(client, org_id, alias="acme-sbx"):
    (client / "config" / "test-users.json").write_text(json.dumps(
        {"schema_version": 1, "org_id_18": org_id, "alias": alias, "users": {}}), encoding="utf-8")


def test_org_is_compared_with_the_test_user_seed(offline):
    _seed(offline, ORG)
    run = Command("--target-org", "acme-sbx", "--json")
    run.get()
    assert run.done() == 0 and run.events()[1]["org_check"] == "org matches this client's test-user seed"
    _seed(offline, ORG[:15])
    run = Command("--target-org", "acme-sbx", "--json")
    run.get()
    assert run.done() == 0, "a 15-character org ID is the same org"


def test_another_org_than_the_client_record_is_refused_before_any_address(offline):
    _seed(offline, OTHER_ORG)
    run = Command("--target-org", "acme-sbx", wait=False)
    assert run.done() == login.EXIT_ORG_MISMATCH
    text = run.err.getvalue()
    assert ORG in text and OTHER_ORG in text and "test-user seed" in text and "Nothing was signed in" in text
    assert run.out.getvalue() == "" and FakeCli.mints() == 0


def test_org_is_compared_with_the_consent_record(offline):
    (offline / "consent.json").write_text(json.dumps({"approved_orgs": [
        {"alias": "acme-sbx", "org_id_18": OTHER_ORG, "kind": "sandbox"},
        {"alias": "acme-prod", "org_id_18": ORG, "kind": "production"}]}), encoding="utf-8")
    run = Command("--target-org", "acme-sbx", wait=False)
    assert run.done() == login.EXIT_ORG_MISMATCH and "consent record" in run.err.getvalue()
    FakeCli.reset(**{"acme-prod": {"is_sandbox": False}})
    run = Command("--target-org", "acme-prod", "--json")
    run.get()
    assert run.done() == 0 and run.events()[1]["org_check"] == "org matches this client's consent record"


def test_a_record_for_another_alias_or_an_unreadable_one_is_not_a_check(offline):
    _seed(offline, OTHER_ORG, alias="acme-uat")
    (offline / "consent.json").write_text("{not json", encoding="utf-8")
    run = Command("--target-org", "acme-sbx", "--json")
    run.get()
    assert run.done() == 0 and run.events()[1]["org_check"] == login.NOT_CHECKED


def test_refused_in_a_connected_workspace_with_the_reason_and_the_alternative(monkeypatch):
    monkeypatch.setattr(login.auth, "in_connected_mode", lambda: True)
    run = Command("--target-org", "acme-sbx", wait=False)
    assert run.done() == login.EXIT_POLICY
    text = run.err.getvalue()
    assert "connected mode" in text and "torque browser browser FLOW --target-org ALIAS" in text
    assert FakeCli.calls == [] and run.out.getvalue() == ""


@pytest.mark.parametrize("argv", [
    ("--target-org", "acme-sbx", "--path", "lightning/page/home"),
    ("--target-org", "acme-sbx", "--path", "//other.example/x"),
    ("--target-org", "acme-sbx", "--path", "https://other.example/x"),
    ("--target-org", "acme-sbx", "--path", "/a\nb"),
    ("--target-org", "acme-sbx", "--timeout", "0"),
    ("--target-org", "acme-sbx", "--api-version", "latest"),
    ("--target-org", "--json"),
])
def test_options_that_cannot_be_used(argv):
    try:
        run = Command(*argv, wait=False)
    except SystemExit as stop:          # argparse's own refusal
        assert stop.code == login.EXIT_USAGE
        return
    assert run.done() == login.EXIT_USAGE and FakeCli.calls == []


def test_path_and_api_version_reach_the_cli_call():
    run = Command("--target-org", "acme-sbx", "--path", "/lightning/o/Account/list?filterName=Recent&x=1",
                  "--api-version", "62.0")
    run.get()
    run.done()
    assert ("mint", "acme-sbx", "/lightning/o/Account/list?filterName=Recent&x=1", "62.0") in FakeCli.calls


# The sentinel (A10): a marker inside the fake Salesforce address must not appear in
# anything the command prints, logs or raises, on any path.

def _paths():
    def success():
        run = Command("--target-org", "acme-sbx", "--json")
        run.get(headers={})
        run.get(path="/wrong")
        run.get()
        return run

    def failing_cli(kind):
        def scenario():
            FakeCli.reset(**{"acme-sbx": {"mint_error": SfError(f"sf org open exit 1 (X): {ADDRESS}", kind)}})
            run = Command("--target-org", "acme-sbx")
            run.get()
            return run
        return scenario

    def identity_error():
        FakeCli.reset(**{"acme-sbx": {"identity_error": SfError(f"stderr said {ADDRESS}", "other")}})
        return Command("--target-org", "acme-sbx", "--json", wait=False)

    def mismatch():
        FakeCli.reset(**{"acme-sbx": {"mint_org_id": OTHER_ORG}})
        run = Command("--target-org", "acme-sbx")
        run.get()
        return run

    def timeout():
        return Command("--target-org", "acme-sbx", "--timeout", "1")

    return [("success", success), ("not authorized", failing_cli("not_authorized")),
            ("api only", failing_cli("api_only")), ("other failure", failing_cli("other")),
            ("identity failure", identity_error), ("org changed", mismatch), ("timeout", timeout)]


@pytest.mark.parametrize("name,scenario", _paths(), ids=[name for name, _ in _paths()])
def test_sentinel_never_leaves_the_redirect(name, scenario, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    run = scenario()
    run.done()
    captured = capsys.readouterr()
    seen = run.everything() + captured.out + captured.err + caplog.text
    assert MARKER not in seen and "frontdoor.jsp" not in seen, name
    for event in run.events():
        assert MARKER not in json.dumps(event)


def test_sentinel_is_not_in_the_minted_object_or_in_errors():
    minted = relay.Minted(ADDRESS, ORG, "admin@acme.example")
    assert MARKER not in repr(minted) and MARKER not in str(minted)
    error = SfError(f"sf org open exit 1: {ADDRESS}")
    assert MARKER not in str(error) and MARKER not in repr(error)
    failure = login.from_sf_error(error, "acme-sbx", "the sign-in request")
    assert MARKER not in failure.reason and MARKER not in str(failure)


# A2: the CLI call is an argument list through Torque's launcher. A stub program named
# sf (a batch file on Windows) receives a path with &, quotes, spaces and % whole.

AWKWARD_PATH = '/apex/Page?id=1&x=2 & echo "q" %OS% ^ (a) b'


def _stub_sf(folder: Path) -> Path:
    """A stand-in `sf` on PATH that records its arguments and answers like `org open`."""
    folder.mkdir(parents=True)
    (folder / ".offline-fixture-bin").write_text("", encoding="utf-8")   # see scripts/offline_support.py
    record = folder / "argv.json"
    program = folder / "stub_sf.py"
    program.write_text(
        "import json, sys\n"
        f"open({str(record)!r}, 'w', encoding='utf-8').write(json.dumps(sys.argv[1:]))\n"
        f"print(json.dumps({{'status': 0, 'result': {{'url': {ADDRESS!r}, 'orgId': {ORG!r}, "
        "'username': 'admin@acme.example'}}))\n", encoding="utf-8")
    if os.name == "nt":
        (folder / "sf.cmd").write_text(f'@echo off\r\n"{sys.executable}" "{program}" %*\r\n', encoding="utf-8")
    else:
        launcher = folder / "sf"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{program}" "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
    return record


def test_an_awkward_path_reaches_the_cli_as_one_argument(tmp_path, monkeypatch):
    record = _stub_sf(tmp_path / "tool dir")
    monkeypatch.setenv("PATH", str(record.parent) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FORCE_OPEN_URL", "/should/not/be/used")
    answer = SfClient("acme-sbx").org_frontdoor(path=AWKWARD_PATH, api_version="62.0")
    assert answer == {"url": ADDRESS, "org_id": ORG, "username": "admin@acme.example"}
    assert json.loads(record.read_text(encoding="utf-8")) == [
        "org", "open", "--target-org", "acme-sbx", "--url-only", "--json", "--path", AWKWARD_PATH,
        "--api-version", "62.0"]


def test_the_cli_call_never_uses_a_shell_or_org_display(monkeypatch):
    seen = []

    def run_tool(argv, **options):
        seen.append((argv, options))
        return type("Done", (), {"returncode": 0, "stderr": "", "stdout": json.dumps(
            {"result": {"url": ADDRESS, "orgId": ORG, "username": "admin@acme.example",
                        "records": [{"Id": ORG, "IsSandbox": True, "OrganizationType": "Developer Edition"}]}})})()
    monkeypatch.setattr("jsc_browser_tests.sf_client.run_tool", run_tool)
    client = SfClient("acme-sbx")
    client.org_frontdoor(path=AWKWARD_PATH)
    assert client.org_identity() == {"org_id": ORG, "is_sandbox": True, "organization_type": "Developer Edition"}
    for argv, options in seen:
        assert isinstance(argv, list) and argv[0] == "sf" and not options.get("shell")
        assert argv[1:3] != ["org", "display"]
    assert seen[0][0][-2:] == ["--path", AWKWARD_PATH]
    assert "FORCE_OPEN_URL" not in seen[0][1]["env"]
    assert seen[1][0][1:5] == ["data", "query", "--query", "SELECT Id, IsSandbox, OrganizationType FROM Organization"]


@pytest.mark.parametrize("stdout,stderr,kind", [
    ('{"name": "NamedOrgNotFoundError", "message": "No authorization information found for acme-sbx."}', "", "not_authorized"),
    ('{"name": "RefreshTokenAuthError", "message": "Error authenticating with the refresh token due to: expired access/refresh token"}', "", "not_authorized"),
    ('{"name": "FrontdoorURLError", "message": "Failed to generate a frontdoor URL.", "data": {"error": "Bad_OAuth_Token"}}', "", "api_only"),
    ('{"name": "SomethingElse", "message": "boom"}', "", "other"),
    ("not json", "plain text failure", "other"),
])
def test_cli_failures_are_classified_and_redacted(monkeypatch, stdout, stderr, kind):
    def run_tool(argv, **options):
        return type("Done", (), {"returncode": 1, "stdout": stdout, "stderr": stderr + " " + ADDRESS})()
    monkeypatch.setattr("jsc_browser_tests.sf_client.run_tool", run_tool)
    with pytest.raises(SfError) as raised:
        SfClient("acme-sbx").org_frontdoor()
    assert raised.value.kind == kind and MARKER not in str(raised.value)


def test_a_missing_cli_and_a_slow_cli_are_told_apart(monkeypatch):
    import subprocess

    def missing(argv, **options):
        raise FileNotFoundError("sf")
    monkeypatch.setattr("jsc_browser_tests.sf_client.run_tool", missing)
    with pytest.raises(SfError) as raised:
        SfClient("acme-sbx").org_identity()
    assert raised.value.kind == "cli_missing"

    def slow(argv, **options):
        raise subprocess.TimeoutExpired(argv, 60, output=ADDRESS)
    monkeypatch.setattr("jsc_browser_tests.sf_client.run_tool", slow)
    with pytest.raises(SfError) as raised:
        SfClient("acme-sbx").org_frontdoor()
    assert raised.value.kind == "other" and "did not answer" in str(raised.value) and MARKER not in str(raised.value)
