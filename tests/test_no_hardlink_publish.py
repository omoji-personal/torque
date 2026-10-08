"""New files on a filesystem without hard links (Google Drive for desktop, exFAT,
some network shares). `os.link` is replaced; no such volume is needed."""
import errno
import json
import os
from pathlib import Path

import pytest
from torque import template_updates as updates
from torque import workspace as ws

ALPHA = ".claude/commands/alpha.md"


def refusal(kind):
    """What os.link raises there: Windows ERROR_INVALID_FUNCTION, or ENOTSUP."""
    if kind == "winerror":
        exc = OSError(errno.EINVAL, "Incorrect function")
        exc.winerror = 1
        return exc
    return OSError(errno.ENOTSUP, "Operation not supported")


@pytest.fixture(params=["winerror", "enotsup"])
def no_links(request, monkeypatch):
    calls = []

    def link(source, destination, **kwargs):
        calls.append(destination)
        raise refusal(request.param)
    monkeypatch.setattr(os, "link", link)
    return calls


def leftovers(root):
    return [path for path in root.rglob("*") if path.name.startswith((".torque-", ".probe-"))
            and path.name != ".torque"]


def put(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


@pytest.fixture
def environment(tmp_path, monkeypatch):
    package = tmp_path / "package"
    put(package, "data/commands/alpha.md", "alpha v1\n")
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    (root / "clients").mkdir()
    put(root, "workspace.json", json.dumps({"schema": "torque.workspace/1", "name": "Synthetic", "profile": "generic"}))
    monkeypatch.setattr(updates.resources, "files", lambda _: package)
    monkeypatch.setattr(updates, "__version__", "test-v1")
    return root, package


def test_workspace_client_and_session_are_written_without_hard_links(tmp_path, no_links):
    root = ws.init_workspace(tmp_path / "firm", "Example firm")
    client = ws.add_client(root, "Alpha Client", "alpha-sandbox")
    entry = ws.add_session(root, "alpha-client", "Recorded on a volume without hard links")
    assert no_links, "the hard link is still tried first"
    assert json.loads((client / "client.json").read_text(encoding="utf-8"))["slug"] == "alpha-client"
    assert (client / "context.md").read_text(encoding="utf-8").startswith("# Alpha Client")
    assert [row["id"] for row in ws.list_sessions(root, "alpha-client")] == [entry["id"]]
    assert (root / updates.MANIFEST).is_file() and (root / ".claude/commands/help.md").is_file()
    assert not leftovers(root)


def test_existing_file_is_never_replaced_without_hard_links(tmp_path, no_links):
    target = put(tmp_path, "notes.md", "local content")
    with pytest.raises(ws.WorkspaceError, match="already exists"):
        ws.atomic_write_new(target, "new content")
    assert target.read_text(encoding="utf-8") == "local content"
    assert not leftovers(tmp_path)


def test_existing_client_is_never_replaced_without_hard_links(tmp_path, no_links):
    root = ws.init_workspace(tmp_path / "firm", "Example firm")
    client = ws.add_client(root, "Alpha Client")
    before = (client / "client.json").read_bytes()
    with pytest.raises(ws.WorkspaceError, match="already exists"):
        ws.add_client(root, "alpha client", "other-org")
    assert (client / "client.json").read_bytes() == before


def test_another_link_failure_is_not_retried_another_way(tmp_path, monkeypatch):
    def link(source, destination, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(os, "link", link)
    with pytest.raises(OSError, match="No space"):
        ws.atomic_write_new(tmp_path / "notes.md", "new content")
    assert not (tmp_path / "notes.md").exists() and not leftovers(tmp_path)
    assert ws.links_unsupported(refusal("winerror")) and ws.links_unsupported(refusal("enotsup"))
    assert not ws.links_unsupported(FileExistsError(errno.EEXIST, "File exists"))


def test_upgrade_adds_and_tracks_files_without_hard_links(environment, no_links):
    root, package = environment
    report = updates.update_templates(root)
    assert report["counts"] == {"add": 1} and report["manifest_updated"]
    assert (root / ALPHA).read_text(encoding="utf-8") == "alpha v1\n"
    manifest = json.loads((root / updates.MANIFEST).read_text(encoding="utf-8"))
    assert set(manifest["files"]) == {ALPHA}
    assert not leftovers(root) and not list(root.rglob(".torque-update-*"))
    assert set(updates.update_templates(root, check=True)["counts"]) == {"current"}


@pytest.mark.parametrize("kind", ["winerror", "enotsup"])
def test_file_appearing_at_publication_is_preserved_without_hard_links(environment, monkeypatch, kind):
    root, package = environment

    def link(source, destination, **kwargs):
        # POSIX passes the name relative to an open folder; Windows the full path.
        if Path(destination).name == "alpha.md":
            put(root, ALPHA, "local file appeared after comparison")
        raise refusal(kind)
    monkeypatch.setattr(os, "link", link)
    report = updates.update_templates(root)
    assert (root / ALPHA).read_text(encoding="utf-8") == "local file appeared after comparison"
    manifest = json.loads((root / updates.MANIFEST).read_text(encoding="utf-8"))
    assert ALPHA not in manifest["files"]
    assert any(row["path"] == ALPHA and row["reason"] == "changed_during_update" for row in report["conflicts"])
    assert not list(root.rglob(".torque-update-*"))


def test_probe_drafts_are_written_without_hard_links(tmp_path, no_links):
    from jsc_probes.cli import _write_pair
    first, second = tmp_path / "ExampleTest.cls", tmp_path / "ExampleTest.cls-meta.xml"
    _write_pair({first: "class body\n", second: "<meta/>\n"}, force=False)
    assert first.read_text(encoding="utf-8") == "class body\n" and second.read_text(encoding="utf-8") == "<meta/>\n"
    with pytest.raises(FileExistsError):
        _write_pair({first: "replacement\n"}, force=False)
    assert first.read_text(encoding="utf-8") == "class body\n"
    assert not leftovers(tmp_path)


def test_probe_draft_appearing_at_publication_is_preserved(tmp_path, monkeypatch):
    from jsc_probes.cli import _write_pair
    first, second = tmp_path / "ExampleTest.cls", tmp_path / "ExampleTest.cls-meta.xml"

    def link(source, destination, **kwargs):
        if Path(destination).name == second.name:
            second.write_text("appeared", encoding="utf-8")
        raise refusal("winerror")
    monkeypatch.setattr(os, "link", link)
    with pytest.raises(FileExistsError):
        _write_pair({first: "class body\n", second: "<meta/>\n"}, force=False)
    # The file this call already published is withdrawn; the other writer's stays.
    assert not first.exists() and second.read_text(encoding="utf-8") == "appeared"
    assert not leftovers(tmp_path)
