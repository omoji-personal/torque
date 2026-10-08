"""Connected mode behind the Antigravity hook: which sessions are bound, and what a
bound one may do. The hook input is the one Antigravity was observed to send; no
Antigravity session is run, so none of this is a live check."""
from collections import namedtuple
import io
import json
import os
from pathlib import Path
import sys

import pytest

from torque import consent, gate, gate_antigravity as agy, hosts, launch, workspace as ws
from torque.presence import Presence

Org = namedtuple("Org", "org_id_18 detected_org_type is_production instance_url", defaults=(None,))
ORGS = {"acme-prod": Org("00D000000000002AAA", "production", True, "https://acme.my.salesforce.com"),
        "acme-sbx": Org("00D000000000001AAA", "sandbox", False, "https://acme--sbx.sandbox.my.salesforce.com")}
YES = lambda: Presence(True, "")
READ = "sf data query -q x -o acme-sbx"
WRITE = "sf project deploy start -m Flow:Case_Escalation -o acme-sbx"


@pytest.fixture
def w(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    root = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(root, "Acme")
    ws.add_client(root, "Beta")
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    letter = tmp_path / "a.pdf"
    letter.write_bytes(b"agreement")
    consent.record_consent(root, "Acme", "2026-09-30", letter, ["metadata", "records"], ["acme-prod", "acme-sbx"],
                           ["Contact"], presence=YES, resolve=ORGS.get)
    consent.sign_off(root, "Acme", "Reviewer", presence=YES)
    # The hook writes these in its own process (here, the test's): set first, so
    # the teardown takes them out again. An empty value is an unset one.
    for name in ("CLAUDE_PROJECT_DIR", hosts.HOOK_HOST_ENV, "TORQUE_CLIENT", "TORQUE_LAUNCH"):
        monkeypatch.setenv(name, "")
    return Path(os.path.realpath(root))


def launched(monkeypatch, root, host="antigravity"):
    """A launch record for this process and the environment `torque launch` sets."""
    record = launch.write_launch_record(root, "Acme", "human", host=host)
    monkeypatch.setenv("TORQUE_CLIENT", record["client"])
    monkeypatch.setenv("TORQUE_LAUNCH", record["id"])
    return record


def payload(root, name, args, **extra):
    return {"toolCall": {"name": name, "args": args}, "workspacePaths": [root.as_posix()],
            "conversationId": "c1", "stepIdx": 1, **extra}


def answer(monkeypatch, capsys, data):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(data).encode("utf-8")), encoding="utf-8"))
    assert agy.main() == 0
    return json.loads(capsys.readouterr().out)


def test_a_session_launched_for_antigravity_is_bound_to_its_client(w, monkeypatch, capsys):
    launched(monkeypatch, w)

    def call(name, args):
        return answer(monkeypatch, capsys, payload(w, name, args))
    # A read of a consented org passes the gate; Antigravity's own flow still asks.
    assert call("run_command", {"CommandLine": READ})["decision"] == "ask"
    assert call("view_file", {"AbsolutePath": str(w / "clients" / "acme" / "context.md")})["decision"] == "allow"
    other = call("view_file", {"AbsolutePath": str(w / "clients" / "beta" / "context.md")})
    assert other["decision"] == "deny" and "bound to acme" in other["reason"]
    assert call("run_command", {"CommandLine": "sf data query -q x -o beta-prod"})["decision"] == "deny"
    write = call("run_command", {"CommandLine": WRITE})
    assert write["decision"] == "deny" and write["reason"].startswith("Connected mode") and "approval" in write["reason"]
    unknown = call("run_command", {"CommandLine": "python3 x.py"})
    assert unknown["decision"] == "ask" and "cannot check" in unknown["reason"]
    admin = call("run_command", {"CommandLine": "torque approval grant req-000000000001 --workspace . --client acme"})
    assert admin["decision"] == "deny"
    unreadable = call("send_command_input", {})
    assert unreadable["decision"] == "deny" and hosts.ANTIGRAVITY.tool_hint in unreadable["reason"]
    # The binding is still in place after every call.
    assert os.environ["TORQUE_CLIENT"] == "acme" and os.environ[hosts.HOOK_HOST_ENV] == "antigravity"


def test_the_call_id_reaches_the_activity_log(w, monkeypatch, capsys):
    launched(monkeypatch, w)
    reply = answer(monkeypatch, capsys, payload(w, "run_command", {"CommandLine": WRITE + " --dry-run"}, stepIdx=7))
    assert reply["decision"] == "ask"
    log = (w / "clients" / "acme" / "approvals" / "activity.jsonl").read_text(encoding="utf-8").splitlines()
    row = json.loads(log[-1])
    assert row["action"] == "check-only" and (row["session_id"], row["tool_use_id"]) == ("c1", "c1:7")


def test_an_approved_write_is_used_once_and_carries_the_call_id(w, monkeypatch, capsys, tmp_path):
    from torque import approval, before_state, changes
    launched(monkeypatch, w)
    cid = changes.create_change(w, "Acme", "Flow fix", "Cases escalate", [], "acme-sbx")["id"]
    source = tmp_path / "b" / "flows"
    source.mkdir(parents=True)
    (source / "Case_Escalation.flow-meta.xml").write_text("<Flow/>", encoding="utf-8")
    before = before_state.import_before_state(w, "Acme", cid, tmp_path / "b")
    flows = w / "force-app" / "main" / "default" / "flows"
    flows.mkdir(parents=True)
    (flows / "Case_Escalation.flow-meta.xml").write_text("<Flow>v2</Flow>", encoding="utf-8")
    request = approval.create_request(w, "Acme", cid, "acme-sbx", argv=WRITE.split(), resolve=ORGS.get,
                                      before_state_event=before["event_id"], cwd=w)
    granted = approval.grant(w, "Acme", request["id"], presence=YES, confirm=lambda: True, out=io.StringIO(),
                             resolve=ORGS.get)
    call = payload(w, "run_command", {"CommandLine": WRITE}, stepIdx=4)
    # The gate lets the approved call through; Antigravity's own flow still asks.
    assert answer(monkeypatch, capsys, call)["decision"] == "ask"
    used = json.loads((w / "clients" / "acme" / "approvals" / "consumed" / granted["id"]).read_text(encoding="utf-8"))
    assert (used["session_id"], used["tool_use_id"]) == ("c1", "c1:4")
    again = answer(monkeypatch, capsys, {**call, "stepIdx": 5})
    assert again["decision"] == "deny" and "used" in again["reason"]


def test_a_session_that_was_not_launched_is_unbound(w, monkeypatch, capsys):
    reply = answer(monkeypatch, capsys, payload(w, "run_command", {"CommandLine": READ}))
    assert reply["decision"] == "deny" and "no client is bound" in reply["reason"]
    notes = {"AbsolutePath": str(w / "clients" / "acme" / "context.md")}
    assert answer(monkeypatch, capsys, payload(w, "view_file", notes))["decision"] == "deny"
    # Naming a client by hand binds nothing.
    monkeypatch.setenv("TORQUE_CLIENT", "acme")
    reply = answer(monkeypatch, capsys, payload(w, "run_command", {"CommandLine": READ}))
    assert reply["decision"] == "deny" and "no client is bound" in reply["reason"]


def test_a_record_written_for_claude_code_does_not_bind_an_antigravity_session(w, monkeypatch, capsys):
    launched(monkeypatch, w, host="claude")
    reply = answer(monkeypatch, capsys, payload(w, "run_command", {"CommandLine": READ}))
    assert reply["decision"] == "deny" and "no client is bound" in reply["reason"]


def test_a_record_written_for_antigravity_does_not_bind_under_claude_codes_hook(w, monkeypatch, capsys):
    record = launched(monkeypatch, w)
    # Claude Code's hook states no host.
    monkeypatch.delenv(hosts.HOOK_HOST_ENV)
    event = {"hook_event_name": "PreToolUse", "cwd": str(w), "session_id": "s1", "tool_use_id": "t1",
             "permission_mode": "default", "tool_name": "Bash", "tool_input": {"command": READ}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    assert gate.main() == 2 and "no client is bound" in capsys.readouterr().err
    # The same record does bind under the hook it was written for.
    reply = answer(monkeypatch, capsys, payload(w, "run_command", {"CommandLine": READ}))
    assert reply["decision"] == "ask" and record["host"] == "antigravity"


def test_the_binding_holds_only_in_the_folder_the_session_was_started_in(w, monkeypatch, capsys, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    # As Antigravity starts a hook: from .agents, so the session's folder is known without the paths too.
    monkeypatch.chdir(w / ".agents")
    for paths in ([elsewhere.as_posix(), w.as_posix()], [(w / "clients").as_posix()], [], None):
        launched(monkeypatch, w)
        call = payload(w, "run_command", {"CommandLine": READ, "Cwd": str(w)})
        if paths is None:
            del call["workspacePaths"]
        else:
            call["workspacePaths"] = paths
        reply = answer(monkeypatch, capsys, call)
        assert reply["decision"] == "deny" and "no client is bound" in reply["reason"], paths
        # The binding left this process: nothing later in the call could use it.
        assert "TORQUE_LAUNCH" not in os.environ and "TORQUE_CLIENT" not in os.environ
    # A folder added to the session after the workspace does not move the binding.
    launched(monkeypatch, w)
    call = payload(w, "run_command", {"CommandLine": READ})
    call["workspacePaths"].append(elsewhere.as_posix())
    assert answer(monkeypatch, capsys, call)["decision"] == "ask"


def test_a_session_whose_record_says_binding_is_refused_where_the_gate_would_ask(w, monkeypatch, capsys):
    """A binding launch is unattended. A real one needs tier 2 (see
    test_launch_binding.py); here the record is altered by hand, so the gate does
    not bind it, and the hook still refuses rather than asks."""
    record = launched(monkeypatch, w)
    path = w / "clients" / "acme" / "approvals" / "consumed" / f"{record['id']}.launch"
    path.write_text(json.dumps({**record, "via": "binding"}), encoding="utf-8")
    reply = answer(monkeypatch, capsys, payload(w, "run_command", {"CommandLine": "python3 x.py"}))
    assert reply["decision"] == "deny" and "cannot check" in reply["reason"]
    assert reply["reason"].endswith("there is no one to ask and the call is refused.")
    # In a presence launch a person is there to answer, so the ask stands.
    launched(monkeypatch, w)
    reply = answer(monkeypatch, capsys, payload(w, "run_command", {"CommandLine": "python3 x.py"}))
    assert reply["decision"] == "ask" and "no one to ask" not in reply["reason"]


def test_antigravitys_browser_tools_are_refused_like_a_browser_server(w, monkeypatch, capsys):
    launched(monkeypatch, w)
    for name, args in (
            ("open_browser_url", {"Url": "https://acme--sbx.sandbox.lightning.force.com/lightning/setup/home"}),
            ("browser_click_element", {"PageId": "1", "Selector": "#save"}),
            ("execute_browser_javascript", {"PageId": "1", "Code": "document.forms[0].submit()"}),
            ("read_browser_page", {"PageId": "1"}),
            ("browser_subagent", {"Task": "save the page layout"})):
        reply = answer(monkeypatch, capsys, payload(w, name, args))
        assert reply["decision"] == "deny" and "browser" in reply["reason"], (name, reply)
    # A network read that is not a browser tool stays with Antigravity's own flow.
    assert answer(monkeypatch, capsys, payload(w, "read_url_content", {"Url": "https://example.com"}))["decision"] == "ask"
    assert answer(monkeypatch, capsys, payload(w, "search_web", {"Query": "x"}))["decision"] == "ask"
