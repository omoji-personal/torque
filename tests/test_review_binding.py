import io
import json
import os
import shlex

import pytest

from delegated_helpers import (CLEAN, FAKE_OWNER, MODEL, ORGS, WRITE, YES, as_agent, delegated_workspace,
                               flow_request)
from torque import approval, delegation, workspace as ws

pytestmark = pytest.mark.skipif(not hasattr(os, "getuid"), reason="tier 2 is POSIX only")
FLOW = "force-app/main/default/flows/Case_Escalation.flow-meta.xml"


def reviewed(root, req):
    return approval.request_view(root, "Acme", req["id"], resolve=ORGS.get)


def grant(root, req, view, **extra):
    # R41 (D5): a delegate must be a separate OS account from the workspace owner; a
    # single-uid test process needs root_owner to stand in for that separate account
    # (delegated_helpers.delegated_grant does the same).
    return approval.grant(root, "Acme", req["id"], delegated=True, model_id=MODEL,
                          request_sha256=view["request_sha256"], payload_digest=view["payload"]["digest"] or "none",
                          out=io.StringIO(), resolve=ORGS.get, root_owner=FAKE_OWNER, **{**CLEAN, **extra})


def test_request_changed_after_review_is_refused(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    view = reviewed(root, req)
    path = root / "clients/acme/approvals/requests" / f"{req['id']}.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "purpose": "changed"}), encoding="utf-8")
    with pytest.raises(delegation.Refusal) as info:
        grant(root, req, view)
    assert info.value.reason_class == "request-changed"


def test_payload_content_changed_after_review_is_refused(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    view = reviewed(root, req)
    (root / FLOW).write_text("<Flow>v3</Flow>", encoding="utf-8")
    with pytest.raises(delegation.Refusal) as info:
        grant(root, req, view)
    assert info.value.reason_class == "payload-changed"


def test_payload_file_set_changed_after_review_is_refused(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    folder = "force-app/main/default/flows"
    req = flow_request(root, ["sf", "project", "deploy", "start", "--source-dir", folder, "--target-org", "acme-dev"])
    view = reviewed(root, req)
    (root / folder / "Extra.flow-meta.xml").write_text("<Flow/>", encoding="utf-8")
    with pytest.raises(delegation.Refusal) as info:
        grant(root, req, view)
    assert info.value.reason_class == "payload-changed"


def test_owner_grant_checks_hashes_only_when_given(tmp_path, monkeypatch):
    # R45 (D5): an AI-kind approver delegate takes no owner (human) approvals at all,
    # so this owner-grant test needs a human-kind approver delegate, like the other
    # owner-grant tests in test_delegated_grant.py.
    root = delegated_workspace(tmp_path, monkeypatch, kind="human")
    req = flow_request(root)
    with pytest.raises(delegation.Refusal):
        approval.grant(root, "Acme", req["id"], presence=YES, confirm=lambda: True, out=io.StringIO(),
                       resolve=ORGS.get, request_sha256="sha256:" + "0" * 64)
    assert approval.grant(root, "Acme", req["id"], presence=YES, confirm=lambda: True, out=io.StringIO(),
                          resolve=ORGS.get)["approver_kind"] == "human"


def test_consumption_time_payload_check_is_kept(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    grant(root, req, reviewed(root, req))
    (root / FLOW).write_text("<Flow>v4</Flow>", encoding="utf-8")
    as_agent(monkeypatch)
    ok, why = approval.consume(root, "Acme", approval.call_key_for_command(shlex.join(WRITE)), "acme-dev",
                               config=ws.load_workspace(root)[1], cwd=root)
    assert not ok and "changed after the approval" in why


# --- V2 I3: payload binding refuses unreadable content, never skips an unreadable ---
# folder, and a delegated approver reads payload files only under the request's
# working folder ({cwd}/**, docs/delegated-approver.md "Paths the approver account needs").

def _deploy(folder):
    return ["sf", "project", "deploy", "start", "--source-dir", str(folder), "--target-org", "acme-dev"]


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads any file")
def test_an_unreadable_payload_file_refuses_instead_of_hashing_a_constant(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    view = reviewed(root, req)
    flow = root / FLOW
    flow.chmod(0)
    try:
        with pytest.raises(ws.WorkspaceError, match="cannot be read"):
            approval.payload_digest(WRITE, root)
        with pytest.raises(delegation.Refusal) as info:
            grant(root, req, view)
    finally:
        flow.chmod(0o644)
    assert info.value.reason_class == "request-changed" and "cannot be read" in str(info.value)


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads any folder")
def test_an_unreadable_payload_folder_is_not_skipped(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    flows = root / "force-app/main/default/flows"
    req = flow_request(root, _deploy("force-app/main/default"))
    locked = flows / "locked"
    locked.mkdir()
    (locked / "Hidden.flow-meta.xml").write_text("<Flow/>", encoding="utf-8")
    locked.chmod(0)
    try:
        with pytest.raises(ws.WorkspaceError, match="cannot be read"):
            approval.payload_digest(_deploy("force-app/main/default"), root)
    finally:
        locked.chmod(0o755)
    assert req["id"]


def test_payload_root_check(tmp_path):
    """Non-skipping unit test for the delegated payload roots: every path the
    payload would read must resolve (links included) under the working folder."""
    cwd = tmp_path / "project"
    (cwd / "force-app").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "data.json").write_text("[]", encoding="utf-8")
    assert approval.payload_root_problems(_deploy("force-app"), cwd) == []
    assert approval.payload_root_problems(_deploy(outside), cwd)
    assert approval.payload_root_problems(_deploy("../outside"), cwd)
    (cwd / "link").symlink_to(outside)
    assert approval.payload_root_problems(_deploy("link"), cwd)
    (cwd / "plan.json").write_text(json.dumps([{"sobject": "Contact", "files": ["../outside/data.json"]}]),
                                   encoding="utf-8")
    plan = ["sf", "data", "import", "tree", "--plan", "plan.json", "--target-org", "acme-dev"]
    assert approval.payload_root_problems(plan, cwd)
    (cwd / "sfdx-project.json").write_text(json.dumps({"packageDirectories": [{"path": "../outside"}]}),
                                           encoding="utf-8")
    metadata = ["sf", "project", "deploy", "start", "--metadata", "Flow:X", "--target-org", "acme-dev"]
    assert approval.payload_root_problems(metadata, cwd)


def test_a_delegated_grant_never_reads_a_payload_outside_the_working_folder(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    outside = tmp_path / "outside" / "flows"
    outside.mkdir(parents=True)
    (outside / "Case_Escalation.flow-meta.xml").write_text("<Flow>outside</Flow>", encoding="utf-8")
    req = flow_request(root, _deploy(outside))
    with pytest.raises(ws.WorkspaceError, match="outside the working folder"):
        reviewed(root, req)
    sha = approval.load_request_hashed(root, "Acme", req["id"])[1]
    touched = []
    real_scandir = os.scandir
    monkeypatch.setattr(os, "scandir", lambda p=".": touched.append(str(p)) or real_scandir(p))
    with pytest.raises(delegation.Refusal) as info:
        approval.grant(root, "Acme", req["id"], delegated=True, model_id=MODEL, request_sha256=sha,
                       payload_digest="none", out=io.StringIO(), resolve=ORGS.get, root_owner=FAKE_OWNER, **CLEAN)
    assert info.value.reason_class == "request-changed" and "outside the working folder" in str(info.value)
    assert not [p for p in touched if str(tmp_path / "outside") in os.path.realpath(p)]
