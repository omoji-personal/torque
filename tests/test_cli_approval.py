from collections import namedtuple
import json
import os

import pytest

from torque import cli, cli_approval, workspace as ws
from torque.presence import Presence

YES = lambda: Presence(True, "")


@pytest.fixture
def connected(tmp_path):
    root = ws.init_workspace(tmp_path / "w", "Firm")
    ws.add_client(root, "Acme")
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    return root


def test_launch_refuses_without_consent(connected):
    with pytest.raises(ws.WorkspaceError, match="consent"):
        cli_approval.launch(connected, "Acme", [], execvp=lambda *a: None, presence=YES)


def test_launch_refuses_without_operator(connected):
    with pytest.raises(ws.WorkspaceError, match="terminal"):
        cli_approval.launch(connected, "Acme", [], execvp=lambda *a: None,
                            presence=lambda: Presence(False, "needs a real terminal"))


def test_launch_refuses_outside_connected_mode(tmp_path):
    root = ws.init_workspace(tmp_path / "w", "Firm")
    ws.add_client(root, "Acme")
    with pytest.raises(ws.WorkspaceError, match="connected"):
        cli_approval.launch(root, "Acme", [], execvp=lambda *a: None, presence=YES)


def test_launch_binds_client_in_environment(connected, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in ("TORQUE_CLIENT", "TORQUE_WORKSPACE"):
        # setenv first so teardown restores the original state after launch() sets it.
        monkeypatch.setenv(key, "placeholder")
        monkeypatch.delenv(key)
    monkeypatch.setattr("torque.consent.load_consent", lambda w, c: {"schema": "torque.consent/1", "client": "acme",
                                                                     "status": "active",
                                                                     "reviewer": {"name": "R", "signed_off_at":
                                                                                  "2026-09-30T10:00:00+00:00"},
                                                                     "data_allowed": ["metadata"],
                                                                     "approved_orgs": [{"alias": "a", "kind": "sandbox",
                                                                                        "org_id_18": "x"}]})
    seen = {}

    def fake_exec(program, args):
        seen.update(program=program, args=args, client=os.environ.get("TORQUE_CLIENT"),
                    workspace=os.environ.get("TORQUE_WORKSPACE"), cwd=os.getcwd())

    cli_approval.launch(connected, "Acme", ["--model", "x"], execvp=fake_exec, presence=YES)
    assert seen["program"] == "claude" and seen["args"] == ["claude", "--model", "x"]
    assert seen["client"] == "acme" and seen["workspace"] == str(connected / "clients" / "acme")
    assert os.path.realpath(seen["cwd"]) == os.path.realpath(connected)


def test_require_exit_codes(connected, monkeypatch, capsys):
    monkeypatch.setattr("torque.approval.require", lambda *a, **k: (False, "no granted approval matches"))
    code = cli.main(["approval", "require", "--workspace", str(connected), "--client", "Acme", "--org", "a",
                     "--", "sf", "apex", "run", "-o", "a"])
    assert code == 3 and "no granted approval" in capsys.readouterr().err
    seen = {}

    def ok(*a, **k):
        seen["argv"] = a[3]
        return True, "apr-000000000001"
    monkeypatch.setattr("torque.approval.require", ok)
    assert cli.main(["approval", "require", "--workspace", str(connected), "--client", "Acme", "--org", "a",
                     "--", "sf", "apex", "run", "-o", "a"]) == 0
    assert seen["argv"] == ["sf", "apex", "run", "-o", "a"]


def test_ai_access_connected_parses(tmp_path, monkeypatch):
    root = ws.init_workspace(tmp_path / "w", "Firm")
    monkeypatch.setattr("torque.presence.operator_present", lambda *a, **k: Presence(True, ""))
    monkeypatch.setattr("torque.presence.confirm_code", lambda *a, **k: True)
    assert cli.main(["workspace", "ai-access", "connected", "--approval", "required", "--path", str(root)]) == 0
    assert json.loads((root / "workspace.json").read_text(encoding="utf-8"))["approval"] == "required"


def test_ai_access_connected_refused_from_agent(tmp_path, capsys):
    root = ws.init_workspace(tmp_path / "w", "Firm")
    assert cli.main(["workspace", "ai-access", "connected", "--approval", "required", "--path", str(root)]) == 2
    assert "terminal" in capsys.readouterr().err


def test_consent_and_request_through_the_cli(connected, tmp_path, monkeypatch, capsys):
    Org = namedtuple("Org", "org_id_18 detected_org_type")
    monkeypatch.setattr("torque.presence.operator_present", lambda *a, **k: Presence(True, ""))
    monkeypatch.setattr("torque.presence.confirm_code", lambda *a, **k: True)
    monkeypatch.setattr("jsc_revert.org_detect.resolve_org",
                        lambda alias, **k: Org("00D000000000001AAA", "sandbox") if alias == "acme-sbx" else None)
    monkeypatch.setattr("jsc_revert.intent_marker._current_user_name", lambda: "consultant")
    letter = tmp_path / "agreement.pdf"
    letter.write_bytes(b"synthetic")
    base = ["--workspace", str(connected), "--client", "Acme"]
    assert cli.main(["client", "consent", "record", *base, "--agreed-on", "2026-09-30", "--evidence", str(letter),
                     "--data", "metadata", "--org", "acme-sbx", "--suspend-contact", "Contact"]) == 0
    assert cli.main(["client", "consent", "sign-off", *base, "--reviewer", "Reviewer"]) == 0
    capsys.readouterr()
    assert cli.main(["client", "consent", "show", *base, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "active"
    from torque import changes
    cid = changes.create_change(connected, "Acme", "Fix", "Works", [], "acme-sbx")["id"]
    monkeypatch.chdir(tmp_path)
    (tmp_path / "x.apex").write_text("System.debug(1);", encoding="utf-8")
    assert cli.main(["approval", "request", *base, "--change", cid, "--org", "acme-sbx", "--",
                     "sf", "apex", "run", "-f", "x.apex", "-o", "acme-sbx"]) == 0
    out = capsys.readouterr().out
    assert "torque approval grant req-" in out and "sf apex run -f x.apex -o acme-sbx" in out
    assert cli.main(["approval", "list", *base, "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert len(listed["requests"]) == 1 and listed["approvals"] == []
    assert cli.main(["approval", "log", *base]) == 0
    assert "approval_request" in capsys.readouterr().out


def test_invocation_is_recorded():
    cli.main(["workflows", "list", "--json"])
    assert cli.INVOCATION == ("torque", ["workflows", "list", "--json"])
