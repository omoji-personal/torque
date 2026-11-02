"""Workspace upgrades without false conflicts: line endings, customizations of unchanged
defaults, a readable summary, and the --diff and --accept steps. Synthetic workspaces only."""
import contextlib
import hashlib
import io
import json

import pytest
from torque import cli
from torque import template_updates as updates
from torque import workspace as ws

QA = ".claude/commands/qa.md"
RULE = ".claude/rules/evidence-and-qa.md"


def invoke(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


def recorded(root):
    data = json.loads((root / updates.MANIFEST).read_text(encoding="utf-8"))
    return {**data.get("antigravity_files", {}), **data["files"]}


def packaged(path):
    return updates._bundled()[path][0].decode("utf-8")


def edit(root, relative, line):
    path = root / relative
    path.write_text(path.read_text(encoding="utf-8") + line, encoding="utf-8", newline="\n")
    return path.read_bytes()


def to_crlf(path):
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))


def upstream(monkeypatch, changes):
    """Stand for a later release whose bundle changes these files ({path: new text})."""
    original = updates._bundled

    def bundled(mode="full"):
        files = original(mode)
        for path, text in changes.items():
            files[path] = (text.encode("utf-8"), files[path][1])
        return files
    monkeypatch.setattr(updates, "_bundled", bundled)


def row(report, path):
    return next(item for item in report["actions"] if item["path"] == path)


@pytest.fixture
def firm(tmp_path):
    return ws.init_workspace(tmp_path / "firm", "Example firm")


def test_line_ending_only_differences_are_not_conflicts(firm):
    # Git with core.autocrlf, or a Windows editor, rewrote every managed file with CRLF.
    for relative in recorded(firm):
        to_crlf(firm / relative)
    report = updates.update_templates(firm, check=True)
    assert not report["conflicts"]
    assert set(report["counts"]) == {"current"}, report["counts"]


def test_untouched_default_saved_with_crlf_takes_the_new_packaged_text(firm, monkeypatch):
    to_crlf(firm / RULE)
    newer = packaged(RULE) + "- A line a later release adds.\n"
    upstream(monkeypatch, {RULE: newer})
    report = updates.update_templates(firm)
    assert row(report, RULE)["action"] == "update" and row(report, RULE)["applied"]
    # Written with LF, as every packaged file is.
    assert (firm / RULE).read_bytes() == newer.encode("utf-8")


def test_customization_of_an_unchanged_default_is_kept_without_a_conflict(firm):
    edited = edit(firm, QA, "\nFirm step: attach the QA sheet.\n")
    base = recorded(firm)[QA]
    for check in (True, False, True):
        report = updates.update_templates(firm, check=check)
        assert row(report, QA)["action"] == "customized" and not report["conflicts"], report["counts"]
    assert (firm / QA).read_bytes() == edited
    assert recorded(firm)[QA] == base


def test_customization_whose_default_changed_is_still_a_conflict(firm, monkeypatch):
    edited = edit(firm, QA, "\nFirm step: attach the QA sheet.\n")
    upstream(monkeypatch, {QA: packaged(QA) + "\nA step a later release adds.\n"})
    report = updates.update_templates(firm)
    conflict = next(item for item in report["conflicts"] if item["path"] == QA)
    assert conflict["action"] == "preserve" and conflict["reason"] == "modified_local"
    assert (firm / QA).read_bytes() == edited


def test_copies_from_another_generator_stay_quiet_until_the_package_changes(firm, monkeypatch):
    # The pattern a real workspace showed: its own script regenerates every .agents/ copy from
    # its .claude/ files, and two recipes carry local edits. Nothing packaged changed since the
    # manifest recorded those files, so none of them is a conflict.
    generated = sorted(path for path in recorded(firm) if path.startswith(".agents/"))
    assert len(generated) > 50
    for relative in generated:
        path = firm / relative
        path.write_text("---\ndescription: written by a local generator\n---\n\n" + path.read_text(encoding="utf-8"),
                        encoding="utf-8", newline="\n")
    edit(firm, QA, "\nFirm step.\n")
    edit(firm, ".claude/commands/diagnose.md", "\nFirm step.\n")
    report = updates.update_templates(firm, check=True)
    assert not report["conflicts"]
    assert report["counts"]["customized"] == len(generated) + 2
    # A later release changes one edited recipe and one generated copy: exactly those two need review.
    copy = ".agents/rules/evidence-and-qa.md"
    upstream(monkeypatch, {QA: packaged(QA) + "\nNew step.\n", copy: packaged(copy) + "- New rule.\n"})
    report = updates.update_templates(firm, check=True)
    assert sorted(item["path"] for item in report["conflicts"]) == [copy, QA]
    assert report["counts"]["customized"] == len(generated) + 2 - 2


def test_accept_records_a_reviewed_customization(firm, monkeypatch):
    # A file Torque never recorded (an older workspace's own copy) needs a decision once.
    data = json.loads((firm / updates.MANIFEST).read_text(encoding="utf-8"))
    del data["files"][QA]
    (firm / updates.MANIFEST).write_text(json.dumps(data), encoding="utf-8")
    (firm / QA).write_text("The firm's own QA recipe.\n", encoding="utf-8", newline="\n")
    report = updates.update_templates(firm, check=True)
    assert [item["reason"] for item in report["conflicts"] if item["path"] == QA] == ["unmanaged_local"]
    code, output, error = invoke("workspace", "upgrade", str(firm), "--accept", QA)
    assert code == 0 and error == "" and QA in output
    assert (firm / QA).read_text(encoding="utf-8") == "The firm's own QA recipe.\n"
    assert recorded(firm)[QA]["sha256"] == hashlib.sha256(packaged(QA).encode("utf-8")).hexdigest()
    report = updates.update_templates(firm, check=True)
    assert row(report, QA)["action"] == "customized" and not report["conflicts"]
    # The next packaged change to it asks for review again.
    upstream(monkeypatch, {QA: packaged(QA) + "\nNew step.\n"})
    assert [item["path"] for item in updates.update_templates(firm, check=True)["conflicts"]] == [QA]


def test_accept_takes_a_folder_and_refuses_what_is_not_managed(firm):
    for relative in (".agents/rules/evidence-and-qa.md", ".agents/agents/field-audit.md"):
        data = json.loads((firm / updates.MANIFEST).read_text(encoding="utf-8"))
        data.get("antigravity_files", {}).pop(relative, None)
        (firm / updates.MANIFEST).write_text(json.dumps(data), encoding="utf-8")
        (firm / relative).write_text("Local copy.\n", encoding="utf-8", newline="\n")
    assert len(updates.update_templates(firm, check=True)["conflicts"]) == 2
    report = updates.accept_templates(firm, [".agents"])
    assert sorted(item["path"] for item in report["actions"] if item["action"] == "accepted") == [
        ".agents/agents/field-audit.md", ".agents/rules/evidence-and-qa.md"]
    assert not updates.update_templates(firm, check=True)["conflicts"]
    for wrong in ("clients/notes.md", "nothing-here/"):
        with pytest.raises(ws.WorkspaceError, match="managed"):
            updates.accept_templates(firm, [wrong])
    code, _, error = invoke("workspace", "upgrade", str(firm), "--check", "--accept", QA)
    assert code == 2 and "--check" in error


def test_diff_compares_the_local_file_with_the_packaged_text(firm):
    edit(firm, QA, "\nFirm step: attach the QA sheet.\n")
    code, output, error = invoke("workspace", "upgrade", str(firm), "--diff", QA)
    assert code == 0 and error == ""
    assert "+Firm step: attach the QA sheet." in output and "torque/data/commands/qa.md" in output
    code, output, _ = invoke("workspace", "upgrade", str(firm), "--diff", RULE)
    assert code == 0 and "no difference" in output.lower()


def test_default_output_is_a_summary_and_json_stays_complete(firm, monkeypatch):
    code, text, error = invoke("workspace", "upgrade", str(firm), "--check")
    assert code == 0 and error == ""
    assert len(text.encode("utf-8")) < 1500, len(text)
    assert "current" in text
    with pytest.raises(ValueError):
        json.loads(text)
    code, raw, _ = invoke("workspace", "upgrade", str(firm), "--check", "--json")
    report = json.loads(raw)
    assert code == 0 and report["counts"] == {"current": len(report["actions"])} and len(report["actions"]) > 100
    # A conflict is listed with its path, its reason and the command that compares it.
    edit(firm, QA, "\nFirm step.\n")
    upstream(monkeypatch, {QA: packaged(QA) + "\nNew step.\n"})
    code, text, _ = invoke("workspace", "upgrade", str(firm), "--check")
    line = next(line for line in text.splitlines() if QA in line)
    assert "--diff" in text and "conflict" in line.lower()
    assert code == 0
