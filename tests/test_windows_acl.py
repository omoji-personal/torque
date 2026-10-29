"""Owner-only files on Windows: the checker everywhere, the real descriptors on Windows."""
import os
import re

import pytest

from jsc_common import winacl
from torque import approval, workspace as ws

USER = "S-1-5-21-1-2-3-1001"
OTHER = "S-1-5-21-1-2-3-1002"
PRIVATE = [("allow", 0x1F01FF, winacl.SYSTEM_SID), ("allow", 0x1F01FF, winacl.ADMINISTRATORS_SID),
           ("allow", 0x1F01FF, USER)]
windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows DACLs")
LOOSE_FOLDER = "(A;OICI;FR;;;WD)"


def test_private_descriptor_has_no_problem():
    assert winacl.acl_problem(USER, PRIVATE, USER) == ""
    assert winacl.acl_problem(USER, PRIVATE + [("deny", 0x1F01FF, OTHER)], USER) == ""
    assert winacl.acl_problem(USER, PRIVATE + [("allow", 0x100000, OTHER)], USER) == ""  # SYNCHRONIZE only


def test_a_null_dacl_is_refused():
    assert winacl.acl_problem(USER, None, USER) == "access"


@pytest.mark.parametrize("mask", [0x1, 0x2, 0x4, 0x8, 0x10, 0x20, 0x40, 0x80, 0x100, 0x10000, 0x40000, 0x80000,
                                  0x120089, 0x1301BF, 0x1F01FF, 0x80000000, 0x40000000, 0x10000000, 0x2000000])
def test_any_data_attribute_security_delete_or_generic_right_for_anyone_else_is_refused(mask):
    assert winacl.acl_problem(USER, PRIVATE + [("allow", mask, "S-1-1-0")], USER) == "access"
    assert winacl.acl_problem(USER, PRIVATE + [("allow", mask, OTHER)], USER) == "access"


def test_an_entry_type_that_is_not_understood_fails_closed():
    assert winacl.acl_problem(USER, PRIVATE + [("other", 0, "")], USER) == "access"


@pytest.mark.parametrize("owner", [OTHER, winacl.ADMINISTRATORS_SID, winacl.SYSTEM_SID, "S-1-5-32-545", ""])
def test_only_the_current_user_may_own_the_file(owner):
    # Administrators included: a file made in an elevated shell is owned by that group, and any
    # administrator could then replace it.
    assert winacl.acl_problem(owner, PRIVATE, USER) == "owner"


def sids(path):
    owner, entries = winacl.read_security(path)
    return owner, sorted(sid for kind, mask, sid in entries), [kind for kind, mask, sid in entries]


def expected():
    return sorted([winacl.SYSTEM_SID, winacl.ADMINISTRATORS_SID, winacl.current_user_sid()])


def assert_private(path):
    owner, entries, kinds = sids(path)
    assert owner == winacl.current_user_sid() and entries == expected() and set(kinds) == {"allow"}
    assert re.search(r"D:P", winacl._sddl(path)) and winacl.problem(path) == ""


@windows_only
def test_the_reader_refuses_a_null_dacl_and_a_weak_right_in_a_real_descriptor(tmp_path):
    target = tmp_path / "approval.key"
    target.write_bytes(b"x" * 32)
    winacl.protect(target, "D:NO_ACCESS_CONTROL")
    assert winacl.read_security(target)[1] is None and winacl.problem(target) == "access"
    # SDDL "CC" is FILE_READ_DATA on a file: Everyone could read the key.
    winacl.protect(target, winacl.private_sddl() + "(A;;CC;;;WD)")
    assert winacl.problem(target) == "access"
    winacl.protect(target)
    assert winacl.problem(target) == ""


@windows_only
def test_a_file_is_protected_from_its_first_byte(tmp_path, monkeypatch):
    winacl.protect(tmp_path, winacl.private_sddl() + LOOSE_FOLDER)
    # Nothing may rely on protecting a file after it is open.
    monkeypatch.setattr(winacl, "protect", lambda *a, **k: pytest.fail("protected after creation"))
    plain = tmp_path / "plain.txt"
    plain.write_text("x")
    fd = ws.create_private(tmp_path / "new.bin")
    try:
        assert_private(tmp_path / "new.bin")  # checked while the creating handle is still open and empty
        os.write(fd, b"secret")
    finally:
        os.close(fd)
    source = tmp_path / "source.bin"
    source.write_bytes(b"content")
    ws.copy_private(source, tmp_path / "copy.bin")
    assert_private(tmp_path / "copy.bin")
    assert (tmp_path / "copy.bin").read_bytes() == b"content"
    ws.atomic_write_new(tmp_path / "grant.json", "{}\n", private=True)
    assert_private(tmp_path / "grant.json")
    ws._atomic_replace_text(plain, "y\n", private=True)
    assert_private(plain)


@windows_only
def test_the_approval_key_is_private_when_created(tmp_path, monkeypatch):
    key = tmp_path / "torque" / "approval.key"
    key.parent.mkdir()
    winacl.protect(key.parent, winacl.private_sddl() + LOOSE_FOLDER)
    monkeypatch.setattr(approval, "key_path", lambda: key)
    monkeypatch.setattr(winacl, "protect", lambda *a, **k: pytest.fail("protected after creation"))
    secret = approval._key(True)
    assert_private(key)
    assert approval._key(False) == secret


@windows_only
def test_the_creating_user_owns_the_file_not_the_group(tmp_path):
    fd = ws.create_private(tmp_path / "owned.bin")
    os.close(fd)
    assert winacl.read_security(tmp_path / "owned.bin")[0] == winacl.current_user_sid()


@windows_only
def test_the_repair_the_message_names_makes_the_key_pass(tmp_path, monkeypatch):
    key = tmp_path / "torque" / "approval.key"
    key.parent.mkdir()
    winacl.protect(key.parent, winacl.private_sddl() + LOOSE_FOLDER)
    monkeypatch.setattr(approval, "key_path", lambda: key)
    key.write_bytes(b"x" * 32)  # an older release's key, with the folder's loose inherited access
    winacl.protect(key, winacl.private_sddl() + "(A;;FR;;;WD)")  # and an explicit grant that icacls /inheritance:r keeps
    with pytest.raises(ws.WorkspaceError) as refused:
        approval._key(False)
    message = str(refused.value)
    command = re.search(r"Remove-Item -LiteralPath '([^']+)'$", message)
    assert command and command[1] == str(key) and "$env" not in message and "icacls" not in message
    os.remove(command[1])  # what Remove-Item does
    assert approval._key(False) is None
    secret = approval._key(True)  # the next grant creates the key again
    assert approval._key(False) == secret and winacl.problem(key) == ""


@windows_only
def test_an_ordinary_workspace_file_keeps_the_inherited_acl(tmp_path):
    """Templates, rules and other workspace files stay readable to whoever the folder allows."""
    winacl.protect(tmp_path, winacl.private_sddl() + LOOSE_FOLDER)
    plain = tmp_path / "AGENTS.md"
    ws.atomic_write_new(plain, "text\n")
    assert winacl.problem(plain) == "access"
    assert "(A;ID;" in winacl._sddl(plain) and "D:P" not in winacl._sddl(plain)
    replaced = tmp_path / "workspace.json"
    ws.atomic_write_new(replaced, "{}\n")
    ws._atomic_replace_text(replaced, '{"a": 1}\n')
    assert winacl.problem(replaced) == "access"


@windows_only
def test_published_records_are_private_but_ordinary_files_are_not(tmp_path):
    """Change records, events, evidence and session entries are written through the production
    paths; in a folder that hands Everyone read, they still come out private (as 0600 does on
    POSIX), and an ordinary workspace file keeps the folder's access."""
    from torque import changes
    winacl.protect(tmp_path, winacl.private_sddl() + LOOSE_FOLDER)
    root = ws.init_workspace(tmp_path / "w", "Synthetic")
    ws.add_client(root, "Sample")
    # Folders created later inherit the loose entry only if it is inheritable: make the client's so.
    record = changes.create_change(root, "Sample", "Example", "Example outcome", [], "sample-sbx")
    change_root = changes.load_change(root, "Sample", record["id"])[0]
    winacl.protect(change_root / "events", winacl.private_sddl() + LOOSE_FOLDER)
    changes.add_note(root, "Sample", record["id"], "Synthetic note", "decision")
    event = sorted((change_root / "events").glob("*.json"))[-1]
    assert_private(event)
    assert_private(change_root / "change.json")
    sessions = root / "clients" / "sample" / "sessions"
    sessions.mkdir(exist_ok=True)
    winacl.protect(sessions, winacl.private_sddl() + LOOSE_FOLDER)
    ws.add_session(root, "Sample", "Synthetic summary", "prepared")
    assert_private(sorted(sessions.glob("*.json"))[-1])
    loose = tmp_path / "loose"
    loose.mkdir()
    winacl.protect(loose, winacl.private_sddl() + LOOSE_FOLDER)
    ordinary = loose / "README-synthetic.md"
    ws.atomic_write_new(ordinary, "text\n")
    assert winacl.problem(ordinary) == "access"
