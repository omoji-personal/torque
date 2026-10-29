"""Claude Code reads the workspace instructions and keeps its own commands: the generated
CLAUDE.md imports AGENTS.md, and recipes named like Claude Code commands install as
torque-NAME. Existing workspaces are migrated by `workspace upgrade` without losing edits."""
import contextlib
import hashlib
from importlib import resources
import io
import json
import re

import pytest
from torque import antigravity, cli, hosts
from torque import template_updates as updates
from torque import workspace as ws

RENAMED = ("help", "context", "status", "security-review", "undo")
LEGACY_CLAUDE_MD = ("# Torque workspace\n\nRead AGENTS.md in this directory for the shared workflow, "
                    "then use `torque context --workspace . --client NAME` for the selected client.\n")
MENTION = re.compile(r"(?<![\w/.:~})-])/(" + "|".join(map(re.escape, RENAMED)) + r")(?![\w/-]|\.\w)")


def invoke(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


def recorded(root):
    data = json.loads((root / updates.MANIFEST).read_text(encoding="utf-8"))
    return {**data.get("antigravity_files", {}), **data["files"]}


def source(name):
    """A packaged recipe as the previous release installed it, under its catalogue name."""
    return resources.files("torque").joinpath("data", "commands", f"{name}.md").read_text(encoding="utf-8").encode("utf-8")


def as_previous_release(root):
    """The command files and manifest entries of a workspace made by 2.0.0a21."""
    path = root / updates.MANIFEST
    data = json.loads(path.read_text(encoding="utf-8"))
    for name in RENAMED:
        (root / f".claude/commands/torque-{name}.md").unlink()
        del data["files"][f".claude/commands/torque-{name}.md"]
    for name in (*RENAMED, "diagnose", "session-resume"):
        (root / f".claude/commands/{name}.md").write_bytes(source(name))
        data["files"][f".claude/commands/{name}.md"] = {"sha256": hashlib.sha256(source(name)).hexdigest(),
                                                        "version": "2.0.0a21"}
    path.write_text(json.dumps(data), encoding="utf-8")
    (root / "CLAUDE.md").write_text(LEGACY_CLAUDE_MD, encoding="utf-8", newline="\n")


@pytest.fixture
def firm(tmp_path):
    return ws.init_workspace(tmp_path / "firm", "Example firm")


def test_each_host_names_its_own_commands():
    assert hosts.CLAUDE.builtin_commands == RENAMED
    assert hosts.ANTIGRAVITY.builtin_commands == ("help", "context", "undo") == antigravity.BUILTIN_COMMANDS


def test_generated_claude_md_imports_agents_md(firm):
    lines = (firm / "CLAUDE.md").read_text(encoding="utf-8").splitlines()
    assert "@AGENTS.md" in lines
    agents = (firm / "AGENTS.md").read_text(encoding="utf-8")
    assert "/torque-status" in agents and "/torque-security-review" in agents


def test_recipes_named_like_claude_code_commands_install_as_torque_names(firm):
    installed = {path.stem for path in (firm / ".claude/commands").glob("*.md")}
    assert not installed & set(hosts.CLAUDE.builtin_commands)
    for name in RENAMED:
        text = (firm / f".claude/commands/torque-{name}.md").read_text(encoding="utf-8")
        assert f"# /torque-{name}\n" in text
    assert "`/torque-security-review`" in (firm / ".claude/commands/diagnose.md").read_text(encoding="utf-8")
    assert "Load `/torque-context`, then" in (firm / ".claude/commands/session-resume.md").read_text(encoding="utf-8")
    # No Claude Code file names a renamed recipe by the command Claude Code answers itself.
    for path in (firm / ".claude").rglob("*.md"):
        assert not MENTION.search(path.read_text(encoding="utf-8")), path
    # Antigravity keeps its own list: status and security-review keep their names there.
    assert (firm / ".agents/skills/status/SKILL.md").is_file()
    assert (firm / ".agents/skills/torque-help/SKILL.md").is_file()
    assert "`/security-review`" in (firm / ".agents/skills/diagnose/SKILL.md").read_text(encoding="utf-8")
    assert set(updates.update_templates(firm, check=True)["counts"]) == {"current"}


def test_upgrade_moves_untouched_old_recipes_to_their_new_names(firm):
    as_previous_release(firm)
    check = updates.update_templates(firm, check=True)
    moved = {row["path"]: row for row in check["actions"] if row["action"] == "renamed"}
    assert set(moved) == {f".claude/commands/{name}.md" for name in RENAMED}
    assert all(not row["applied"] and row["renamed_to"] == row["path"].replace("commands/", "commands/torque-")
               for row in moved.values())
    assert (firm / ".claude/commands/help.md").is_file() and "@AGENTS.md" not in (firm / "CLAUDE.md").read_text(
        encoding="utf-8")
    report = updates.update_templates(firm)
    assert not report["conflicts"], report["conflicts"]
    for name in RENAMED:
        assert not (firm / f".claude/commands/{name}.md").exists()
        assert f"# /torque-{name}\n" in (firm / f".claude/commands/torque-{name}.md").read_text(encoding="utf-8")
        assert f".claude/commands/{name}.md" not in recorded(firm)
    assert "`/torque-security-review`" in (firm / ".claude/commands/diagnose.md").read_text(encoding="utf-8")
    assert "@AGENTS.md" in (firm / "CLAUDE.md").read_text(encoding="utf-8").splitlines()
    again = updates.update_templates(firm, check=True)
    assert set(again["counts"]) == {"current"}, again["counts"]


def test_upgrade_moves_a_customized_old_recipe_and_keeps_every_byte(firm):
    as_previous_release(firm)
    old = firm / ".claude/commands/status.md"
    old.write_text(old.read_text(encoding="utf-8") + "\nFirm check: the VPN is up.\n", encoding="utf-8", newline="\n")
    edited = old.read_bytes()
    report = updates.update_templates(firm)
    new = firm / ".claude/commands/torque-status.md"
    assert not old.exists() and new.read_bytes() == edited
    conflict = next(row for row in report["conflicts"] if row["path"] == ".claude/commands/torque-status.md")
    assert conflict["reason"] == "modified_local"
    updates.update_templates(firm)
    assert new.read_bytes() == edited and not old.exists()


def test_old_and_new_recipe_both_present_are_left_for_the_consultant(firm):
    as_previous_release(firm)
    old, new = firm / ".claude/commands/undo.md", firm / ".claude/commands/torque-undo.md"
    old.write_text("Firm undo notes.\n", encoding="utf-8", newline="\n")
    new.write_text("Firm torque-undo notes.\n", encoding="utf-8", newline="\n")
    report = updates.update_templates(firm)
    assert old.read_text(encoding="utf-8") == "Firm undo notes.\n"
    assert new.read_text(encoding="utf-8") == "Firm torque-undo notes.\n"
    assert ".claude/commands/undo.md" in [row["path"] for row in report["conflicts"]]


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_upgrade_replaces_the_previous_generated_claude_md(firm, newline):
    (firm / "CLAUDE.md").write_bytes(LEGACY_CLAUDE_MD.replace("\n", newline).encode("utf-8"))
    before = (firm / "CLAUDE.md").read_bytes()
    check = updates.update_templates(firm, check=True)
    row = next(item for item in check["actions"] if item["path"] == "CLAUDE.md")
    assert row["action"] == "update" and not row["applied"]
    assert (firm / "CLAUDE.md").read_bytes() == before
    updates.update_templates(firm)
    assert "@AGENTS.md" in (firm / "CLAUDE.md").read_text(encoding="utf-8").splitlines()
    assert not any(item["path"] == "CLAUDE.md" for item in updates.update_templates(firm, check=True)["actions"])


def test_upgrade_keeps_an_edited_claude_md_and_says_how_to_import(firm):
    custom = "# Firm notes\n\nRead AGENTS.md first, then the firm notes.\n"
    (firm / "CLAUDE.md").write_text(custom, encoding="utf-8", newline="\n")
    report = updates.update_templates(firm)
    assert (firm / "CLAUDE.md").read_text(encoding="utf-8") == custom
    assert "@AGENTS.md" in report["claude_md"] and not report["conflicts"]
    (firm / "CLAUDE.md").write_text("@AGENTS.md\n\n# Firm notes\n", encoding="utf-8", newline="\n")
    assert "claude_md" not in updates.update_templates(firm, check=True)
    # A mention in a code span is not an import.
    (firm / "CLAUDE.md").write_text("Read `@AGENTS.md` first.\n", encoding="utf-8", newline="\n")
    assert "claude_md" in updates.update_templates(firm, check=True)


def test_workflows_show_reads_the_local_renamed_recipe(firm):
    local = firm / ".claude/commands/torque-context.md"
    local.write_text("FIRM_CONTEXT_RECIPE\n", encoding="utf-8")
    code, output, _ = invoke("workflows", "show", "context", "--workspace", str(firm))
    assert code == 0 and output.strip() == "FIRM_CONTEXT_RECIPE"
    # A workspace that has not been upgraded yet still has the recipe under its old name.
    local.unlink()
    (firm / ".claude/commands/context.md").write_text("FIRM_OLD_NAME\n", encoding="utf-8")
    assert invoke("workflows", "show", "context", "--workspace", str(firm))[1].strip() == "FIRM_OLD_NAME"


def on_antigravity(root):
    config = json.loads((root / ws.CONFIG).read_text(encoding="utf-8"))
    (root / ws.CONFIG).write_text(json.dumps({**config, "host": "antigravity"}), encoding="utf-8")


def shown(root, name):
    code, output, error = invoke("workflows", "show", name, "--workspace", str(root))
    assert code == 0 and error == "", error
    return output


def test_workflows_show_names_the_commands_of_the_workspaces_host(firm):
    # diagnose mentions /security-review: Claude Code answers that itself, Antigravity does not.
    claude = shown(firm, "diagnose")
    assert "/torque-security-review" in claude
    assert claude.rstrip("\n") == (firm / ".claude/commands/diagnose.md").read_text(encoding="utf-8").rstrip("\n")
    on_antigravity(firm)
    agy = shown(firm, "diagnose")
    assert "/security-review" in agy and "/torque-security-review" not in agy
    assert not (firm / ".agents/skills/torque-security-review").exists()
    assert agy.rstrip("\n") == (firm / ".agents/skills/diagnose/SKILL.md").read_text(encoding="utf-8").rstrip("\n")


def test_workflows_show_reads_the_hosts_local_copy_under_its_name(firm):
    on_antigravity(firm)
    # Antigravity renames /context but not /security-review.
    (firm / ".agents/skills/torque-context/SKILL.md").write_text("FIRM_AGY_CONTEXT\n", encoding="utf-8")
    (firm / ".agents/skills/security-review/SKILL.md").write_text("FIRM_AGY_REVIEW\n", encoding="utf-8")
    (firm / ".claude/commands/torque-security-review.md").write_text("FIRM_CLAUDE_REVIEW\n", encoding="utf-8")
    assert shown(firm, "context").strip() == "FIRM_AGY_CONTEXT"
    assert shown(firm, "security-review").strip() == "FIRM_AGY_REVIEW"


@pytest.mark.parametrize("host", ["claude", "antigravity"])
def test_workflows_show_writes_a_missing_recipe_for_the_host(firm, host):
    if host == "antigravity":
        on_antigravity(firm)
        (firm / ".agents/skills/diagnose/SKILL.md").unlink()
        wanted = antigravity.skill("diagnose", source("diagnose").decode("utf-8"))
    else:
        (firm / ".claude/commands/diagnose.md").unlink()
        wanted = ws.claude_copy("commands", "diagnose.md", source("diagnose").decode("utf-8"))[1]
    assert shown(firm, "diagnose").rstrip("\n") == wanted.rstrip("\n")


def test_workflows_show_without_a_workspace_keeps_the_catalogue_names(firm, monkeypatch):
    monkeypatch.delenv("TORQUE_WORKSPACE", raising=False)
    code, output, _ = invoke("workflows", "show", "diagnose")
    assert code == 0 and "/security-review" in output and "/torque-security-review" not in output
    assert output.rstrip("\n") == source("diagnose").decode("utf-8").rstrip("\n")
    assert json.loads(invoke("workflows", "show", "help", "--workspace", str(firm), "--json")[1])["name"] == "help"
