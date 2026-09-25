"""V2 I6 (formerly limit R58): on a system with POSIX access lists (Linux), a file
created 0600 or a folder created 0700 in a folder with a default access list entry
gets an access-list mask of no permissions, which hides the approver's inherited
entry. In a delegated tier 2 workspace, Torque opens the mask (group bits 0640 for
a file, 0750 for a folder) on the files and folders the approver reads, only when
the path has an extended access list. macOS (no POSIX access lists), a workspace
without a delegated approver and a path with no access list keep 0600 and 0700.

The unit tests simulate the access-list check (`os.getxattr` on
`system.posix_acl_access`); the last test uses the real `setfacl` and runs only on
Linux with a file system that supports access lists."""
import errno
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from delegated_helpers import delegated_workspace, flow_request
from torque import changes, workspace as ws

pytestmark = pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX modes")


def fake_acl(monkeypatch, where=lambda p: True):
    """Report an extended access list on every path `where` accepts."""
    def getxattr(path, name, *, follow_symlinks=True):
        if name == ws.ACL_XATTR and where(str(path)):
            return b"\x02\x00\x00\x00"
        raise OSError(errno.ENODATA, "No data available", str(path))
    monkeypatch.setattr(os, "getxattr", getxattr, raising=False)


def mode(path):
    return os.stat(path).st_mode & 0o777


def request_and_change(root):
    req = flow_request(root)
    requests = root / "clients/acme/approvals/requests"
    change = root / "clients/acme/changes" / req["change"]
    events = change / "events"
    return req, requests, change, events


def undelegate(root):
    config = json.loads((root / "workspace.json").read_text())
    config["delegates"].pop("approver")
    (root / "workspace.json").write_text(json.dumps(config))


def fresh_workspace(tmp_path, monkeypatch, delegated=True):
    root = delegated_workspace(tmp_path, monkeypatch)
    # delegated_workspace pre-creates the approvals folders; remove the agent's
    # requests folder and the changes folder so the flow below creates them.
    shutil.rmtree(root / "clients/acme/approvals/requests")
    shutil.rmtree(root / "clients/acme/changes", ignore_errors=True)
    if not delegated:
        undelegate(root)
    return root


def test_with_a_default_acl_the_approvers_files_and_folders_open_the_mask(tmp_path, monkeypatch):
    root = fresh_workspace(tmp_path, monkeypatch)
    fake_acl(monkeypatch)
    req, requests, change, events = request_and_change(root)
    assert mode(requests) == 0o750 and mode(requests / f"{req['id']}.json") == 0o640
    assert mode(root / "clients/acme/changes") == 0o750
    assert mode(change) == 0o750 and mode(change / "change.json") == 0o640
    assert mode(events) == 0o750
    assert {mode(p) for p in events.iterdir()} <= {0o640}
    event = changes.add_note(root, "Acme", req["change"], "Reviewed the flow")
    assert mode(events / f"{event['id']}.json") == 0o640
    (tmp_path / "proof.txt").write_text("proof", encoding="utf-8")
    cid = changes.create_change(root, "Acme", "Checked", "Cases escalate", ["It escalates"], "acme-dev")["id"]
    check = changes.add_check(root, "Acme", cid, "AC1", "pass", "Escalated", evidence=tmp_path / "proof.txt")
    evidence = root / "clients/acme/changes" / cid / check["evidence"]["path"]
    assert mode(evidence) == 0o640 and mode(evidence.parent) == 0o750


def test_without_an_acl_modes_stay_private(tmp_path, monkeypatch):
    root = fresh_workspace(tmp_path, monkeypatch)
    fake_acl(monkeypatch, where=lambda p: False)
    req, requests, change, events = request_and_change(root)
    assert mode(requests) == 0o700 and mode(requests / f"{req['id']}.json") == 0o600
    assert mode(change) == 0o700 and mode(change / "change.json") == 0o600


def test_a_workspace_without_a_delegated_approver_keeps_a15_modes(tmp_path, monkeypatch):
    root = fresh_workspace(tmp_path, monkeypatch, delegated=False)
    fake_acl(monkeypatch)
    req, requests, change, events = request_and_change(root)
    assert mode(requests) == 0o700 and mode(requests / f"{req['id']}.json") == 0o600
    assert mode(change) == 0o700 and mode(change / "change.json") == 0o600


def test_no_getxattr_means_no_change(tmp_path, monkeypatch):
    """macOS has no os.getxattr and no POSIX access lists: nothing is re-moded."""
    root = fresh_workspace(tmp_path, monkeypatch)
    monkeypatch.delattr(os, "getxattr", raising=False)
    req, requests, change, events = request_and_change(root)
    assert mode(requests) == 0o700 and mode(requests / f"{req['id']}.json") == 0o600


def test_an_existing_folder_keeps_its_mode(tmp_path, monkeypatch):
    """Requirement 22: a folder that already exists is never re-moded."""
    root = fresh_workspace(tmp_path, monkeypatch)
    requests = root / "clients/acme/approvals/requests"
    requests.mkdir(mode=0o700)
    fake_acl(monkeypatch)
    req = flow_request(root)
    assert mode(requests) == 0o700 and mode(requests / f"{req['id']}.json") == 0o640


def test_open_mask_leaves_a_link_alone(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.write_text("x")
    target.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(target)
    fake_acl(monkeypatch)
    ws.open_acl_mask(link)
    assert mode(target) == 0o600


@pytest.mark.skipif(not sys.platform.startswith("linux") or not shutil.which("setfacl")
                    or not shutil.which("getfacl"), reason="needs Linux with setfacl and getfacl")
def test_real_default_acl_entry_reaches_a_new_file(tmp_path):
    folder = tmp_path / "shared"
    folder.mkdir(mode=0o700)
    done = subprocess.run(["setfacl", "-d", "-m", "u:65534:rX", str(folder)], capture_output=True)
    if done.returncode != 0:
        pytest.skip("the file system does not support access lists")
    path = folder / "request.json"
    ws.atomic_write_new(path, "{}\n")
    before = getfacl(path)
    assert "mask::---" in before and approver_entry(before) == ("r-x", "---")
    ws.open_acl_mask(path)
    after = getfacl(path)
    # The approver's default entry (r-x) now reaches the file as read only: the
    # mask Torque opened is r--, and the entry's effective permission is r--.
    assert "mask::r--" in after and "other::---" in after and approver_entry(after) == ("r-x", "r--")
    sub = folder / "events"
    sub.mkdir(mode=0o700)
    ws.open_acl_mask(sub)
    assert "mask::r-x" in getfacl(sub)


def getfacl(path) -> str:
    """V2-5: numeric ids (-n), so the uid 65534 entry reads the same whether or
    not the host names that uid (CI's Linux prints it as `nobody` otherwise)."""
    return subprocess.run(["getfacl", "-n", "-p", str(path)], capture_output=True, text=True, check=True).stdout


def approver_entry(listing: str) -> tuple[str, str]:
    """The uid 65534 entry's (permission, effective permission); getfacl prints
    `#effective:` only when the mask removes a bit, else the two are equal."""
    for line in listing.splitlines():
        if line.startswith("user:65534:"):
            perms, _, rest = line[len("user:65534:"):].partition("#effective:")
            return perms.strip(), (rest.strip() or perms.strip())
    raise AssertionError(f"no user:65534 entry in:\n{listing}")


def test_approver_entry_reads_getfacl_numeric_output():
    """V2-5: the parser the Linux-only test above relies on, checked everywhere
    against the shapes getfacl -n prints (a tab before `#effective:`)."""
    listing = "# file: request.json\nuser::rw-\nuser:65534:r-x\t#effective:r--\ngroup::---\nmask::r--\n"
    assert approver_entry(listing) == ("r-x", "r--")
    assert approver_entry("user:65534:r--\nmask::r--\n") == ("r--", "r--")
    with pytest.raises(AssertionError):
        approver_entry("user:nobody:r-x\t#effective:r--\n")
