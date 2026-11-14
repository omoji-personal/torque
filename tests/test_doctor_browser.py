"""`torque doctor` and browser work: the playwright module alone is not a browser, and
the two browser modes are reported separately. Signing a host browser tab in
(`torque browser login`) needs only the Salesforce CLI; Torque's own Playwright browser
needs the module and a browser Playwright downloaded.

Local inspection only: nothing is started and no org is called."""
import contextlib
import io
import json

import pytest

from torque import cli


def doctor(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(["doctor", *args])
    return code, out.getvalue()


@pytest.fixture
def machine(monkeypatch, tmp_path):
    """A machine with the Salesforce CLI and the playwright module, and a browser folder to fill."""
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/tools/{name}" if name in ("sf", "git") else None)
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda name: object() if name in ("yaml", "playwright") else None)
    browsers = tmp_path / "ms-playwright"
    browsers.mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(browsers))
    return browsers


def test_the_module_without_a_browser_is_not_browser_ready(machine):
    code, output = doctor("--for", "browser", "--json")
    report = json.loads(output)
    assert code == 3 and report["ready"] is False
    assert report["capabilities"]["browser"]["missing"] == ["playwright browser"]
    assert report["playwright_browser"] is None and report["org_calls"] is False
    assert any("python -m playwright install chromium" in action and "torque browser login" in action
               for action in report["next_actions"])


def test_host_tab_login_is_ready_with_the_cli_alone(machine):
    code, output = doctor("--for", "browser-login", "--json")
    report = json.loads(output)
    assert code == 0 and report["ready"] is True
    assert report["capabilities"]["browser-login"] == {
        "local_dependencies_ready": True, "missing": [], "live_verified": False,
        "covers": "torque browser login in a host browser tab; needs only the Salesforce CLI"}
    assert "Playwright" in report["capabilities"]["browser"]["covers"]


@pytest.mark.parametrize("folder", ["chromium-1140", "chromium_headless_shell-1140"])
def test_a_downloaded_browser_makes_the_playwright_mode_ready(machine, folder):
    (machine / folder).mkdir()
    code, output = doctor("--for", "browser", "--json")
    report = json.loads(output)
    assert code == 0 and report["ready"] is True and report["capabilities"]["browser"]["missing"] == []
    assert report["playwright_browser"].endswith(folder)


def test_other_folders_are_not_a_browser(machine):
    (machine / "ffmpeg-1010").mkdir()
    (machine / "chromium-1140.tmp").write_text("not a folder", encoding="utf-8")
    assert doctor("--for", "browser", "--json")[0] == 3


def test_the_two_modes_are_printed_separately(machine):
    code, output = doctor("--for", "browser")
    assert code == 3
    assert "Browser sign-in in a host tab (torque browser login): local dependencies ready" in output
    assert "Torque's own Playwright browser: missing playwright browser" in output
    assert "browser: missing local dependencies" in output


def test_without_the_cli_neither_mode_is_ready(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda name: None)
    code, output = doctor("--for", "browser-login", "--json")
    report = json.loads(output)
    assert code == 3 and report["capabilities"]["browser-login"]["missing"] == ["sf"]
    # Without the module a browser cannot be looked for: only the module is named.
    assert report["capabilities"]["browser"]["missing"] == ["sf", "playwright"]
    assert any("needs only the Salesforce CLI" in action for action in report["next_actions"])


def test_browsers_inside_the_package_are_found(monkeypatch, tmp_path):
    package = tmp_path / "site" / "playwright"
    (package / "driver" / "package" / ".local-browsers" / "chromium-1140").mkdir(parents=True)
    spec = type("Spec", (), {"origin": str(package / "__init__.py")})()
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda name: spec if name == "playwright" else None)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")
    assert cli._playwright_browser().endswith("chromium-1140")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "nowhere"))
    assert cli._playwright_browser() is None
