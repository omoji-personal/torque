"""`torque browser login --storage-state PATH`: a session file stays with the selected
client. A path in another client's or an initiative's folder of the workspace is refused
before anything is asked of the CLI; the selected client's folder and a folder outside
any workspace or repository are allowed (a git work tree only where git ignores it)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from login_helpers import Command, FakeCli  # noqa: E402

from jsc_browser_tests import login  # noqa: E402


@pytest.fixture
def ws(tmp_path, monkeypatch):
    FakeCli.reset()
    monkeypatch.setattr(login, "SfClient", FakeCli)
    monkeypatch.setattr(login.auth, "in_connected_mode", lambda: False)
    monkeypatch.setattr(login.auth, "in_build_only_mode", lambda: False, raising=False)
    monkeypatch.delenv("TORQUE_TEST_USERS", raising=False)
    root = tmp_path / "ws"
    for folder in ("clients/acme", "clients/other", "initiatives/research"):
        (root / folder).mkdir(parents=True)
    (root / "clients" / "acme" / "client.json").write_text("{}", encoding="utf-8")
    (root / "workspace.json").write_text('{"ai_access": "full"}', encoding="utf-8")
    monkeypatch.setenv("TORQUE_WORKSPACE", str(root / "clients" / "acme"))
    return root


@pytest.mark.parametrize("where, words", [
    ("clients/other/state/browser-auth/acme-sbx.json", "another client's folder"),
    ("initiatives/research/acme-sbx.json", "an initiative's folder"),
])
def test_another_clients_or_an_initiatives_folder_is_refused(ws, where, words):
    problem = login.storage_path_problem((ws / where).resolve())
    assert problem and words in problem and "selected client's folder" in problem


def test_the_selected_clients_folder_and_a_folder_outside_the_workspace_are_allowed(ws, tmp_path):
    assert login.storage_path_problem((ws / "clients/acme/state/browser-auth/acme-sbx.json").resolve()) is None
    assert login.storage_path_problem((tmp_path / "elsewhere" / "acme-sbx.json").resolve()) is None
    plain = tmp_path / "project" / "clients" / "x"          # a clients/ folder of no workspace
    plain.mkdir(parents=True)
    assert login.storage_path_problem((plain / "s.json").resolve()) is None


def test_a_client_folder_with_no_client_selected_is_refused(ws, monkeypatch):
    monkeypatch.delenv("TORQUE_WORKSPACE")
    monkeypatch.delenv("JSC_ROOT", raising=False)
    problem = login.storage_path_problem((ws / "clients/acme/s.json").resolve())
    assert problem and "--client" in problem


def test_the_command_refuses_before_the_cli_is_asked(ws):
    run = Command("--target-org", "acme-sbx", "--storage-state", str(ws / "clients/other/s.json"), "--json")
    assert run.done() == login.EXIT_USAGE and FakeCli.calls == []
    assert "another client's folder" in run.events()[-1]["reason"]
    assert not (ws / "clients/other/s.json").exists()
