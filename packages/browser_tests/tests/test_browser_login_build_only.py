"""`torque browser login` refuses itself in a build-only workspace, as it does in a connected
one, so that a host with no gate hook wired cannot send a browser into the org there. The
mode is read the way the gate reads it."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from login_helpers import Command, FakeCli  # noqa: E402

from jsc_browser_tests import login  # noqa: E402


def workspace(tmp_path, monkeypatch, config_text):
    root = tmp_path / "ws"
    client = root / "clients" / "acme"
    client.mkdir(parents=True)
    (client / "client.json").write_text(json.dumps({"slug": "acme"}), encoding="utf-8")
    if config_text is not None:
        (root / "workspace.json").write_text(config_text, encoding="utf-8")
    monkeypatch.setenv("TORQUE_WORKSPACE", str(client))
    return client


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    FakeCli.reset()
    monkeypatch.setattr(login, "SfClient", FakeCli)
    monkeypatch.setattr(login.auth, "in_connected_mode", lambda: False)
    monkeypatch.delenv("TORQUE_TEST_USERS", raising=False)


@pytest.mark.parametrize("config", ['{"ai_access": "build-only"}', '{"ai_access": "Full"}', "{not json"],
                         ids=["build-only", "unknown-value", "unreadable"])
@pytest.mark.parametrize("mode", [(), ("--storage-state", "session.json")], ids=["host-tab", "session-file"])
def test_refused_in_a_build_only_workspace_before_the_cli_is_asked(tmp_path, monkeypatch, config, mode):
    client = workspace(tmp_path, monkeypatch, config)
    extra = ("--storage-state", str(client / "state" / "s.json")) if mode else ()
    run = Command("--target-org", "acme-sbx", "--json", *extra)
    assert run.done() == login.EXIT_POLICY and FakeCli.calls == []
    reason = run.events()[-1]["reason"]
    assert reason.startswith("build-only mode:") and "outside the AI session" in reason


@pytest.mark.parametrize("config", ['{"ai_access": "full"}', "{}", None], ids=["full", "default", "no-config"])
def test_a_full_workspace_is_not_refused(tmp_path, monkeypatch, config):
    workspace(tmp_path, monkeypatch, config)
    run = Command("--target-org", "acme-sbx", "--json")
    assert run.get()[0] == 302 and run.done() == login.EXIT_OK
