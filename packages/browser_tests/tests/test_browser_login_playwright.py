"""`torque browser login --storage-state PATH` and `torque browser logout` (offline).

Playwright is a double: no browser is started. The checks are about what the command
does around it: the path rules, the owner-only file, the user check, and that neither
the Salesforce address nor the session's content is ever printed."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from login_helpers import ADDRESS, MARKER, ORG, Command, FakeCli  # noqa: E402

from jsc_browser_tests import cli, login  # noqa: E402

COOKIE = "COOKIE9d2cNEVERPRINT"
USER_ID = "005000000000001AAA"


class FakePage:
    def __init__(self, world):
        self.world = world

    async def goto(self, url, **kwargs):
        self.world["navigated"].append(url)
        if self.world.get("goto_error"):
            raise RuntimeError(f"Page.goto: timeout navigating to {url}")

    async def title(self):
        return self.world.get("title", "Home | Salesforce")

    async def wait_for_selector(self, *args, **kwargs):
        if self.world.get("no_shell"):
            raise TimeoutError(f"waiting for selector at {ADDRESS}")

    async def wait_for_function(self, *args, **kwargs):
        pass

    async def evaluate(self, script, argument=None):
        if argument is None:
            return USER_ID
        return {"status": 200, "username": self.world.get("page_username", "admin@acme.example")}


class FakeContext:
    def __init__(self, world):
        self.world = world

    async def new_page(self):
        return FakePage(self.world)

    async def storage_state(self, **kwargs):
        assert "path" not in kwargs, "the command writes the file itself, owner-only"
        return {"cookies": [{"name": "sid", "value": COOKIE, "domain": "acme--sbx.sandbox.my.salesforce.com"}],
                "origins": []}

    async def close(self):
        self.world["closed"].append("context")


class FakeBrowser:
    def __init__(self, world):
        self.world = world

    async def new_context(self, **kwargs):
        return FakeContext(self.world)

    async def close(self):
        self.world["closed"].append("browser")


@pytest.fixture
def world(monkeypatch, tmp_path):
    """A stand-in for Playwright: `world` records what the browser was asked to do."""
    world = {"navigated": [], "closed": [], "launched": [], "executable": sys.executable}

    class Chromium:
        @property
        def executable_path(self):
            return world["executable"]

        async def launch(self, **kwargs):
            world["launched"].append(kwargs)
            return FakeBrowser(world)

    class Started:
        chromium = Chromium()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    module = types.ModuleType("playwright.async_api")
    module.async_playwright = lambda: Started()
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.async_api", module)
    monkeypatch.setattr(login.importlib.util, "find_spec", lambda name: object())
    FakeCli.reset()
    monkeypatch.setattr(login, "SfClient", FakeCli)
    monkeypatch.setattr(login.auth, "in_connected_mode", lambda: False)
    for name in ("DEBUG", "PWDEBUG", "DEBUG_FILE", "TORQUE_TEST_USERS"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "client" / "config").mkdir(parents=True)
    monkeypatch.setenv("TORQUE_WORKSPACE", str(tmp_path / "client"))
    return world


def run(*argv):
    command = Command("--target-org", "acme-sbx", *argv, wait=False)
    code = command.done()
    return code, command


def test_writes_an_owner_only_session_file_and_prints_the_path_not_the_content(world, tmp_path):
    path = tmp_path / "auth" / "acme-sbx.json"
    code, command = run("--storage-state", str(path), "--json")
    assert code == login.EXIT_OK
    assert world["navigated"] == [ADDRESS] and world["launched"] == [{"headless": True}]
    assert world["closed"] == ["context", "browser"], "Torque's own browser is closed afterwards"
    assert json.loads(path.read_text(encoding="utf-8"))["cookies"][0]["value"] == COOKIE
    event, = command.events()
    assert event == {"event": "saved", "session_verified": True, "storage_state": str(path.resolve()),
                     "org_id": ORG, "org_kind": "sandbox", "org_edition": "Enterprise Edition",
                     "username": "admin@acme.example", "user_id": USER_ID, "org_check": login.NOT_CHECKED}
    assert COOKIE not in command.everything() and MARKER not in command.everything()
    if os.name == "nt":
        from jsc_common import winacl
        assert winacl.problem(path) == "", "only the current user, SYSTEM and Administrators can read it"
    else:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [entry.name for entry in path.parent.iterdir()] == [path.name], "no temporary file is left"


def test_plain_text_names_the_file_and_how_to_delete_it(world, tmp_path):
    path = tmp_path / "acme-sbx.json"
    code, command = run("--storage-state", str(path))
    assert code == login.EXIT_OK
    assert command.out.getvalue().splitlines()[0] == f"Session file written: {path.resolve()}"
    assert f"torque browser logout --storage-state {path.resolve()}" in command.err.getvalue()
    assert COOKIE not in command.everything()


def test_an_existing_session_file_is_replaced_and_stays_private(world, tmp_path):
    path = tmp_path / "acme-sbx.json"
    path.write_text("old", encoding="utf-8")
    assert run("--storage-state", str(path))[0] == login.EXIT_OK
    assert json.loads(path.read_text(encoding="utf-8"))["origins"] == []
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_as_alias_signs_the_scripted_browser_in_as_the_test_user(world, tmp_path):
    FakeCli.reset(**{"acme-sbx": {}, "acme-sbx-std": {"username": "std.user@acme.example"}})
    world["page_username"] = "STD.USER@acme.example"
    code, command = run("--storage-state", str(tmp_path / "std.json"), "--as-alias", "acme-sbx-std", "--json")
    assert code == login.EXIT_OK
    event, = command.events()
    assert event["as_alias"] == "acme-sbx-std" and event["username"] == "STD.USER@acme.example"
    assert ("mint", "acme-sbx-std", None, None) in FakeCli.calls


def test_another_user_in_the_page_than_the_alias_writes_nothing(world, tmp_path):
    world["page_username"] = "someone.else@acme.example"
    path = tmp_path / "acme-sbx.json"
    code, command = run("--storage-state", str(path))
    assert code == login.EXIT_SIGN_IN and not path.exists()
    assert "not the alias's user" in command.err.getvalue() and world["closed"] == ["context", "browser"]


@pytest.mark.parametrize("fault", ["goto_error", "no_shell"])
def test_a_failed_sign_in_writes_nothing_and_never_shows_the_address(world, tmp_path, fault):
    world[fault] = True
    path = tmp_path / "acme-sbx.json"
    code, command = run("--storage-state", str(path), "--json")
    assert code == login.EXIT_SIGN_IN and not path.exists()
    assert MARKER not in command.everything() and "frontdoor" not in command.everything()
    assert command.events()[-1]["event"] == "failed"


def test_a_login_page_after_the_front_door_is_a_failure(world, tmp_path):
    world["title"] = "Login | Salesforce"
    path = tmp_path / "acme-sbx.json"
    code, command = run("--storage-state", str(path))
    assert code == login.EXIT_SIGN_IN and not path.exists() and MARKER not in command.everything()


def test_playwright_not_installed_is_skipped_cleanly(world, tmp_path, monkeypatch):
    monkeypatch.setattr(login.importlib.util, "find_spec", lambda name: None)
    code, command = run("--storage-state", str(tmp_path / "s.json"))
    assert code == login.EXIT_PLAYWRIGHT and FakeCli.calls == []
    text = command.err.getvalue()
    assert "Playwright is not installed" in text and "playwright install chromium" in text
    assert "without --storage-state" in text, "the mode that needs no Playwright is named"


def test_no_playwright_browser_is_skipped_before_any_address_is_asked_for(world, tmp_path):
    world["executable"] = str(tmp_path / "no-such-browser")
    code, command = run("--storage-state", str(tmp_path / "s.json"))
    assert code == login.EXIT_PLAYWRIGHT and FakeCli.mints() == 0 and world["launched"] == []
    assert "no Playwright browser is installed" in command.err.getvalue()


def test_a_path_inside_a_git_work_tree_must_be_ignored(world, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / ".gitignore").write_text("private/\n", encoding="utf-8")
    code, command = run("--storage-state", str(repo / "auth" / "acme-sbx.json"))
    assert code == login.EXIT_USAGE and FakeCli.calls == []
    text = command.err.getvalue()
    assert "inside a git work tree and is not ignored" in text and "Choose a path that git ignores" in text
    assert not (repo / "auth").exists()
    ignored = repo / "private" / "auth" / "acme-sbx.json"
    assert run("--storage-state", str(ignored))[0] == login.EXIT_OK and ignored.is_file()


def test_a_folder_is_not_a_session_file(world, tmp_path):
    assert run("--storage-state", str(tmp_path))[0] == login.EXIT_USAGE


def test_playwright_debug_settings_would_print_the_address(world, tmp_path, monkeypatch):
    monkeypatch.setenv("DEBUG", "pw:api")
    code, command = run("--storage-state", str(tmp_path / "s.json"))
    assert code == login.EXIT_USAGE and FakeCli.calls == [] and "DEBUG is set" in command.err.getvalue()


def test_refused_in_a_connected_workspace(world, tmp_path, monkeypatch):
    monkeypatch.setattr(login.auth, "in_connected_mode", lambda: True)
    code, command = run("--storage-state", str(tmp_path / "s.json"))
    assert code == login.EXIT_POLICY and "saved sessions" in command.err.getvalue() and FakeCli.calls == []


def logout(*argv):
    import io
    out, err = io.StringIO(), io.StringIO()
    args = cli.build_parser().parse_args(["logout", *argv])
    return login.cmd_logout(args, out, err), out.getvalue(), err.getvalue()


def test_logout_deletes_the_session_file(world, tmp_path):
    path = tmp_path / "acme-sbx.json"
    assert run("--storage-state", str(path))[0] == login.EXIT_OK
    code, out, _ = logout("--storage-state", str(path))
    assert code == 0 and not path.exists() and out.startswith("Session file deleted:") and COOKIE not in out
    code, out, _ = logout("--storage-state", str(path), "--json")
    assert code == 0 and json.loads(out) == {"event": "absent", "storage_state": str(path.resolve())}


@pytest.mark.parametrize("content", ["notes about a client", '{"cookies": "x"}', "[]"])
def test_logout_deletes_nothing_else(tmp_path, content):
    path = tmp_path / "notes.json"
    path.write_text(content, encoding="utf-8")
    code, _, err = logout("--storage-state", str(path))
    assert code == login.EXIT_USAGE and path.read_text(encoding="utf-8") == content
    assert "not a browser session file" in err
    assert logout("--storage-state", str(tmp_path))[0] == login.EXIT_USAGE and tmp_path.is_dir()
