"""`torque browser login` under the gate: it follows the browser policy that already
exists, in every mode, and the gate adds no refusal of its own for it.

- full mode (the default): the hook lets it through, with every option.
- build-only: blocked like every other `torque browser` call, with the same message.
- connected: read as Torque's own browser, so it needs a granted browser window like
  `torque browser browser`; the command then refuses itself there (the package's own
  test), because a host tab or a saved session would leave the guard."""
import io
import json
import os
from collections import namedtuple
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from torque import approval, changes, consent, gate, gate_connected as gc, workspace as ws
from torque import connected_routes as cr
from torque.presence import Presence

Org = namedtuple("Org", "org_id_18 detected_org_type is_production instance_url", defaults=(None,))
ORGS = {"acme-sbx": Org("00D000000000001AAA", "sandbox", False, "https://acme--sbx.sandbox.my.salesforce.com")}
YES = lambda: Presence(True, "")
LOGIN = "torque browser login --target-org acme-sbx --workspace . --client Acme"
FORMS = [LOGIN, LOGIN + " --json --timeout 60 --path /lightning/page/home", LOGIN + " --as-alias acme-sbx-std",
         LOGIN + " --as-user 005000000000002AAA", LOGIN + " --storage-state state/auth/acme-sbx.json",
         "torque browser logout --storage-state state/auth/acme-sbx.json"]


@pytest.fixture
def plain(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("TORQUE_CLIENT", raising=False)
    root = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(root, "Acme")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    return Path(os.path.realpath(root))


def hook_blocks(root, command, tool="Bash"):
    event = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {"command": command},
             "cwd": str(root)}
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = gate.evaluate(json.dumps(event))
    return code != 0, err.getvalue()


@pytest.mark.parametrize("command", FORMS)
@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
def test_full_mode_lets_browser_login_through(plain, command, tool):
    assert ws.load_workspace(plain)[1].get("ai_access", "full") == "full"
    blocked, reason = hook_blocks(plain, command, tool)
    assert not blocked, reason
    assert gate.decide(tool, {"command": command}, plain, "full", plain) == (True, "")


def test_build_only_blocks_it_like_every_torque_browser_call(plain):
    ws.set_ai_access(plain, "build-only", presence=YES)
    blocked, reason = hook_blocks(plain, LOGIN)
    other_blocked, other_reason = hook_blocks(plain, "torque browser browser smoke_login --target-org acme-sbx "
                                                     "--workspace . --client Acme")
    assert blocked and other_blocked
    assert "Build-only mode" in reason and "Run it yourself outside the AI session" in reason
    assert reason == other_reason, "the same message as the existing browser command: no new refusal"


@pytest.fixture
def connected(plain, tmp_path, monkeypatch):
    monkeypatch.setattr("jsc_revert.intent_marker._current_user_name", lambda: "consultant")
    ws.set_ai_access(plain, "connected", approval="required", presence=YES)
    letter = tmp_path / "a.pdf"
    letter.write_bytes(b"agreement")
    consent.record_consent(plain, "Acme", "2026-09-30", letter, ["metadata", "records"], ["acme-sbx"], ["Contact"],
                           presence=YES, resolve=ORGS.get)
    consent.sign_off(plain, "Acme", "Reviewer", presence=YES)
    return plain


def decide(root, command):
    return gc.decide_connected("Bash", {"command": command}, root, root, env={"TORQUE_CLIENT": "acme"},
                               permission_mode=None, session_id="s1", tool_use_id="t1")


def test_connected_mode_reads_it_as_torques_own_browser(connected):
    existing = "torque browser browser smoke_login --target-org acme-sbx --workspace . --client Acme"
    assert [(r.kind, r.org) for r in cr.classify("Bash", {"command": LOGIN})] == \
        [(r.kind, r.org) for r in cr.classify("Bash", {"command": existing})] == [("browser_write", "acme-sbx")]
    # Without a browser window: the same refusal as the existing command, naming the request to make.
    refused, known = decide(connected, LOGIN), decide(connected, existing)
    assert refused.action == known.action == "deny"
    assert "browser window" in refused.reason and "torque approval request" in refused.reason
    # With a granted window the gate allows it, as it allows the existing command.
    change = changes.create_change(connected, "Acme", "Change", "Works", [], "acme-sbx")["id"]
    request = approval.create_request(connected, "Acme", change, "acme-sbx", browser_minutes=10,
                                      purpose="Add Tier to the Case layout", resolve=ORGS.get)
    approval.grant(connected, "Acme", request["id"], presence=YES, confirm=lambda: True, out=io.StringIO(),
                   resolve=ORGS.get)
    assert decide(connected, LOGIN).action == decide(connected, existing).action == "allow"
    # A host browser tab stays refused, window or not: nothing was loosened.
    tab = gc.decide_connected("mcp__claude-in-chrome__navigate", {"url": "http://127.0.0.1:50000/code"},
                              connected, connected, env={"TORQUE_CLIENT": "acme"}, permission_mode=None,
                              session_id="s1", tool_use_id="t2")
    assert tab.action == "deny"
    # Deleting a saved session names no org and is local.
    assert decide(connected, "torque browser logout --storage-state state/auth/acme-sbx.json").action == "allow"
