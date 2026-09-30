"""A maintenance flag pauses every record write; reads keep working."""
from unittest.mock import patch

import pytest

from torque import changes
from torque import workspace as ws


@pytest.fixture
def root(tmp_path):
    root = ws.init_workspace(tmp_path / "firm", "Synthetic firm")
    ws.add_client(root, "Alpha")
    return root


def pause(root):
    (root / ".torque").mkdir(exist_ok=True)
    (root / ws.MAINTENANCE_FLAG).write_text("migration\n")


def test_session_write_refused_during_maintenance(root):
    pause(root)
    with pytest.raises(ws.WorkspaceError, match="maintenance"):
        ws.add_session(root, "Alpha", "work", "prepared")
    assert ws.list_sessions(root, "Alpha") == []


def test_client_and_change_writes_refused_during_maintenance(root):
    item = changes.create_change(root, "Alpha", "Title", "Outcome")
    pause(root)
    with pytest.raises(ws.WorkspaceError, match="maintenance"):
        ws.add_client(root, "Beta")
    with pytest.raises(ws.WorkspaceError, match="maintenance"):
        changes.create_change(root, "Alpha", "Another", "Outcome")
    with pytest.raises(ws.WorkspaceError, match="maintenance"):
        changes.add_note(root, "Alpha", item["id"], "decided", "decision")


def test_writes_resume_when_the_flag_is_removed(root):
    pause(root)
    (root / ws.MAINTENANCE_FLAG).unlink()
    assert ws.add_session(root, "Alpha", "work", "prepared")["status"] == "prepared"


# --- Fix round 1 ---


def test_check_evidence_capture_refused_during_maintenance_leaves_no_file(root, tmp_path):
    """Fix round 1 finding 2: require_writable must fire before _capture_file
    copies any bytes, not only afterward in _append. Otherwise a refused
    add_check still leaves a copy of the evidence under the change's
    evidence/ folder."""
    item = changes.create_change(root, "Alpha", "Title", "Outcome", ["Do the thing"])
    evidence = tmp_path / "proof.txt"
    evidence.write_text("proof\n")
    pause(root)
    with pytest.raises(ws.WorkspaceError, match="maintenance"):
        changes.add_check(root, "Alpha", item["id"], "AC1", "pass", "checked", evidence)
    evidence_dir = root / "clients" / "alpha" / "changes" / item["id"] / "evidence"
    assert not evidence_dir.exists() or list(evidence_dir.iterdir()) == []


def test_verify_deploy_refused_during_maintenance_before_dispatch(root):
    """Fix round 1 finding 2: require_writable must fire before verify_deploy
    ever dispatches a live Metadata API call or writes raw evidence."""
    item = changes.create_change(root, "Alpha", "Title", "Outcome")
    pause(root)
    with patch("jsc_qa.dispatcher.dispatch_meta_api") as dispatch:
        with pytest.raises(ws.WorkspaceError, match="maintenance"):
            changes.verify_deploy(root, "Alpha", item["id"], "explicit-dev", "0Af000000000001AAA")
    dispatch.assert_not_called()


# --- Final review: every record and config writer honors the flag ---

import io
import os
from collections import namedtuple
from pathlib import Path

from torque import approval, before_state, consent, delegation, launch, permissions
from torque.presence import Presence

_YES = lambda: Presence(True, "")
_Org = namedtuple("_Org", "org_id_18 detected_org_type is_production instance_url")
_ORGS = {"alpha-sbx": _Org("00D000000000001AAA", "sandbox", False, "https://alpha--sbx.sandbox.my.salesforce.com")}
_ARGV = ["sf", "project", "deploy", "start", "-m", "ApexClass:A", "-o", "alpha-sbx"]
_UID = os.getuid() if hasattr(os, "getuid") else 0


def _connected(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    root = Path(os.path.realpath(ws.init_workspace(tmp_path / "firm", "Synthetic firm")))
    ws.add_client(root, "Alpha")
    ws.set_ai_access(root, "connected", approval="required", presence=_YES)
    letter = tmp_path / "letter.pdf"
    letter.write_bytes(b"agreement")
    consent.record_consent(root, "Alpha", "2026-09-30", letter, ["metadata"], ["alpha-sbx"], [], presence=_YES,
                           resolve=_ORGS.get)
    consent.sign_off(root, "Alpha", "Reviewer", presence=_YES)
    change_id = changes.create_change(root, "Alpha", "Title", "Outcome", [], "alpha-sbx")["id"]
    project = tmp_path / "project"
    (project / "force-app" / "main" / "default" / "classes").mkdir(parents=True)
    (project / "sfdx-project.json").write_text('{"packageDirectories": [{"path": "force-app"}]}', encoding="utf-8")
    (project / "force-app" / "main" / "default" / "classes" / "A.cls").write_text("class", encoding="utf-8")
    request = approval.create_request(root, "Alpha", change_id, "alpha-sbx", argv=_ARGV, resolve=_ORGS.get,
                                      cwd=project)
    return root, {"change": change_id, "request": request["id"], "letter": letter, "tmp": project}


def _tree(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes() if p.is_file() else None, p.stat().st_mode)
            for p in Path(root).rglob("*")}


_REC = {"id": "apr-000000000000", "change": "chg-000000000000", "org_alias": "alpha-sbx", "command": "x",
        "request_id": "req-000000000000", "kind": "browser"}
WRITERS = {
    "consent.record_consent": lambda r, c: consent.record_consent(r, "Alpha", "2026-09-30", c["letter"], ["metadata"],
                                                                  ["alpha-sbx"], [], presence=_YES,
                                                                  resolve=_ORGS.get),
    "consent.sign_off": lambda r, c: consent.sign_off(r, "Alpha", "Reviewer", presence=_YES),
    "consent.suspend": lambda r, c: consent.suspend(r, "Alpha", presence=_YES),
    "approval.create_request": lambda r, c: approval.create_request(r, "Alpha", c["change"], "alpha-sbx", argv=_ARGV,
                                                                    resolve=_ORGS.get, cwd=c["tmp"]),
    "approval.grant": lambda r, c: approval.grant(r, "Alpha", c["request"], presence=_YES, confirm=lambda: True,
                                                  out=io.StringIO(), resolve=_ORGS.get),
    "approval.grant delegated": lambda r, c: approval.grant(r, "Alpha", c["request"], delegated=True, model_id="m",
                                                            env={}, ancestors=lambda: []),
    "approval.deny": lambda r, c: approval.deny(r, "Alpha", c["request"], "no", presence=_YES, confirm=lambda: True),
    "approval.deny_delegated": lambda r, c: approval.deny_delegated(r, "Alpha", c["request"], "other", model_id="m",
                                                                    reason="no", env={}, ancestors=lambda: []),
    "approval.consume": lambda r, c: approval.consume(r, "Alpha", "key", "alpha-sbx",
                                                      config=ws.load_workspace(r)[1]),
    "approval.log_activity": lambda r, c: approval.log_activity(r, "Alpha", {"action": "check-only"}),
    "approval.note_browser_use": lambda r, c: approval.note_browser_use(r, "Alpha", _REC),
    "approval.consumed_for_wrapper": lambda r, c: approval.consumed_for_wrapper(r, "Alpha", ("torque", ["x"]),
                                                                                "alpha-sbx"),
    "approval.release_for_retry": lambda r, c: approval.release_for_retry(r, "Alpha", ("torque", ["x"]), "alpha-sbx"),
    "approval.authorize_child": lambda r, c: approval.authorize_child(r, "Alpha", "apr-000000000000", ["x"]),
    "approval.approved_parent": lambda r, c: approval.approved_parent(r, "Alpha", "apr-000000000000", "alpha-sbx",
                                                                      ("torque", ["x"])),
    "approval.release_child": lambda r, c: approval.release_child(r, "Alpha", "apr-000000000000", "alpha-sbx",
                                                                  ("torque", ["x"])),
    "launch.create_binding": lambda r, c: launch.create_binding(r, "Alpha", model_id="m", env={},
                                                                ancestors=lambda: []),
    "launch.claim_binding": lambda r, c: launch.claim_binding(r, "Alpha", "lnk-000000000000", env={},
                                                              ancestors=lambda: []),
    "launch.write_launch_record": lambda r, c: launch.write_launch_record(r, "Alpha", "human"),
    "before_state.import_before_state": lambda r, c: before_state.import_before_state(r, "Alpha", c["change"],
                                                                                      c["letter"]),
    "before_state.capture_metadata": lambda r, c: before_state.capture_metadata(
        r, "Alpha", c["change"], "alpha-sbx", ["ApexClass:A"], run=_no_run),
    "before_state.capture_records": lambda r, c: before_state.capture_records(
        r, "Alpha", c["change"], "alpha-sbx", ["Account:001000000000001"], run=_no_run),
    "delegation.set_delegate": lambda r, c: delegation.set_delegate(r, "setup", "someone", _UID + 1, "ai",
                                                                    presence=_YES, geteuid=lambda: 0,
                                                                    lookup=lambda name: _UID + 1),
    "permissions.write_settings": lambda r, c: permissions.write_settings(r, presence=_YES, confirm=lambda: True),
    "workspace.set_ai_access": lambda r, c: ws.set_ai_access(r, "full", presence=_YES),
}


def _no_run(*args, **kwargs):
    raise AssertionError("no org call may run while the workspace is in maintenance")


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX workspace setup")
@pytest.mark.parametrize("name", sorted(WRITERS))
def test_every_writer_refuses_during_maintenance_and_writes_nothing(name, tmp_path, monkeypatch):
    root, ctx = _connected(tmp_path, monkeypatch)
    pause(root)
    home = tmp_path / "home"
    before, before_home = _tree(root), (_tree(home) if home.exists() else {})
    with pytest.raises(ws.WorkspaceError, match="maintenance"):
        WRITERS[name](root, ctx)
    assert _tree(root) == before
    assert (_tree(home) if home.exists() else {}) == before_home


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX workspace setup")
def test_wrapper_refuses_cleanly_during_maintenance(tmp_path, monkeypatch, capsys):
    """A wrapper run in a paused connected workspace stops with a message, never a traceback."""
    from jsc_revert.wrappers import _common as c
    root, _ = _connected(tmp_path, monkeypatch)
    pause(root)
    monkeypatch.delenv("TORQUE_WORKSPACE", raising=False)
    monkeypatch.setenv("TORQUE_CLIENT", "alpha")
    monkeypatch.chdir(root)
    assert c.connected_approval("alpha-sbx", "00D000000000001AAA")[0] == c.EXIT_NOT_APPROVED
    assert "maintenance" in capsys.readouterr().err
