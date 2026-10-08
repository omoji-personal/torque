"""The Antigravity surface a workspace gets from init and upgrade; no agent is run."""
import json
import re

import pytest
from torque import antigravity
from torque import template_updates as updates
from torque import workspace as ws

# Tool names a worker may list (an unknown name can hang the worker).
KNOWN_TOOLS = {"view_file", "list_dir", "grep_search", "find_by_name", "run_command", "write_to_file",
               "replace_file_content"}
RULE = "# Evidence and QA\n\n- Keep observed facts and assertions apart.\n"
COMMAND = ('---\ndescription: "Check the workspace: tools and org connections."\n---\n\n# /{name}\n\n'
           "Steps.\n\n**Conversation input:** $ARGUMENTS\nUse supplied context.\n")
WORKER = "---\nname: worker\ndescription: Complete bounded drafting work.\n---\n\nDo the assigned work.\n"


def put(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def header(text):
    """The frontmatter lines of a generated file."""
    match = re.match(r"---\n(.*?)\n---\n\n", text, re.S)
    assert match, text[:80]
    return match.group(1).splitlines()


@pytest.fixture
def environment(tmp_path, monkeypatch):
    package = tmp_path / "package"
    put(package, "data/rules/evidence.md", RULE)
    put(package, "data/commands/status.md", COMMAND.format(name="status"))
    put(package, "data/commands/help.md", COMMAND.format(name="help"))
    put(package, "data/commands/plain.md", "A local-style recipe without a description.\n")
    put(package, "data/agents/worker.md", WORKER)
    put(package, "data/skills/review/SKILL.md", "---\nname: review\ndescription: Review.\n---\n")
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    (root / "clients").mkdir()
    put(root, "workspace.json", json.dumps({"schema": "torque.workspace/1", "name": "Synthetic", "profile": "generic"}))
    monkeypatch.setattr(updates.resources, "files", lambda _: package)
    monkeypatch.setattr(updates, "__version__", "test-v1")
    return root, package


def manifest(root):
    """Every tracked path, from both sections of the manifest."""
    data = json.loads((root / updates.MANIFEST).read_text(encoding="utf-8"))
    return {**data.get("antigravity_files", {}), **data["files"]}


def test_rule_is_always_on_with_its_heading_as_description():
    text = antigravity.rule("evidence", RULE)
    assert header(text) == ["trigger: always_on", "description: Evidence and QA"]
    assert text.endswith(RULE)
    assert header(antigravity.rule("local-notes", "No heading here.\n"))[1] == "description: local-notes"
    # A description that is not a plain YAML value is quoted.
    assert header(antigravity.rule("x", "# Scope: what applies\n"))[1] == 'description: "Scope: what applies"'
    # A rule scoped to some paths would apply everywhere as an always-on copy.
    assert antigravity.rule("scoped", "---\npaths: src/**\n---\n# Scoped\n") is None


def test_recipe_becomes_a_skill_without_the_unexpanded_placeholder():
    text = antigravity.skill("status", COMMAND.format(name="status"))
    assert header(text) == ["name: status", 'description: "Check the workspace: tools and org connections."']
    assert "$ARGUMENTS" not in text and antigravity.INPUT_NOTE in text
    assert antigravity.skill("plain", "No frontmatter, so no description.\n") is None


@pytest.mark.parametrize("name", antigravity.BUILTIN_COMMANDS)
def test_recipe_named_like_a_builtin_command_is_prefixed(name):
    text = antigravity.skill(name, COMMAND.format(name=name))
    assert header(text)[0] == f"name: torque-{name}"
    assert f"# /torque-{name}\n" in text and f"# /{name}\n" not in text


COMMANDS = "Load `/context`, then run /help (or /undo). End with /context.\n"
NOT_COMMANDS = ("Keep project/context and connection/context; see schemas/help and https://help.example.com/undo,\n"
                "/context/notes.md, ~/context, ./help, $(pwd)/undo, /context.md, /contextual and /session-save.\n")


def test_a_mention_of_a_renamed_recipe_points_at_its_antigravity_name():
    source = COMMAND.format(name="session-resume").replace("Steps.\n", COMMANDS + NOT_COMMANDS)
    text = antigravity.skill("session-resume", source)
    assert "Load `/torque-context`, then run /torque-help (or /torque-undo). End with /torque-context.\n" in text
    # A path, an address, another recipe and a longer word are left as they are.
    assert NOT_COMMANDS in text
    # Rules and worker roles are read under Antigravity too.
    assert "Use `/torque-context` first.\n" in antigravity.rule("x", "# X\n\nUse `/context` first.\n")
    assert "Offer /torque-undo.\n" in antigravity.agent("worker", WORKER + "Offer /undo.\n")
    # Doing it twice changes nothing more.
    assert antigravity._commands(antigravity._commands(COMMANDS)) == antigravity._commands(COMMANDS)


def test_the_packaged_resume_recipe_names_the_renamed_context_command(tmp_path):
    root = ws.init_workspace(tmp_path / "firm", "Example firm")
    text = (root / ".agents/skills/session-resume/SKILL.md").read_text(encoding="utf-8")
    assert "Load `/torque-context`, then" in text and "`/context`" not in text
    # Claude Code's copy keeps the name the recipe has there.
    assert "Load `/context`, then" in (root / ".claude/commands/session-resume.md").read_text(encoding="utf-8")
    # No generated file names one of Antigravity's own commands as a Torque recipe.
    from importlib import resources
    generated = antigravity.surface(resources.files("torque").joinpath("data"))
    assert ".agents/skills/torque-context/SKILL.md" in generated
    for relative, (made, _source) in generated.items():
        assert not antigravity._BUILTIN_MENTION.search(made), relative


@pytest.mark.parametrize("declared,expected", [
    (None, list(antigravity.TOOLS.values())),
    ("tools: Read, Grep, WebFetch\n", ["view_file", "grep_search"]),
    ("tools:\n  - Read\n  - Bash\n  - Read\n", ["view_file", "run_command"]),
    ("tools: WebFetch\n", list(antigravity.READ_TOOLS)),
])
def test_worker_lists_only_known_tools(declared, expected):
    source = WORKER.replace("description:", (declared or "") + "description:")
    lines = header(antigravity.agent("worker", source))
    assert lines[:3] == ["name: worker", "description: Complete bounded drafting work.", "tools:"]
    assert lines[3:-2] == ["  - " + tool for tool in expected]
    assert lines[-2:] == ["mainAgent: false", "subagent: true"]
    assert set(expected) <= KNOWN_TOOLS
    assert antigravity.agent("worker", "No frontmatter.\n") is None


def test_init_writes_the_surface_and_records_it(environment):
    root, package = environment
    ws._materialize_workflows(root)
    report = updates.record_initial_templates(root)
    assert not report["conflicts"]
    expected = {".agents/rules/evidence.md", ".agents/skills/status/SKILL.md",
                ".agents/skills/torque-help/SKILL.md", ".agents/agents/worker.md"}
    assert expected <= set(manifest(root))
    assert (root / ".agents/rules/evidence.md").read_text(encoding="utf-8") == antigravity.rule("evidence", RULE)
    # A recipe without a description has no skill; the packaged skill keeps its place.
    assert not (root / ".agents/skills/plain").exists() and not (root / ".agents/skills/help").exists()
    assert (root / ".agents/skills/review/SKILL.md").is_file()
    assert (root / ".claude/commands/help.md").is_file()
    check = updates.update_templates(root, check=True)
    assert set(check["counts"]) == {"current"}


def test_upgrade_adds_the_surface_to_an_older_workspace_and_keeps_local_files(environment):
    root, package = environment
    local = put(root, ".agents/rules/evidence.md", "---\ntrigger: manual\ndescription: Mine\n---\nLocal rule.\n")
    put(root, ".claude/rules/evidence.md", RULE)
    report = updates.update_templates(root)
    assert local.read_text(encoding="utf-8").endswith("Local rule.\n")
    assert ".agents/rules/evidence.md" not in manifest(root)
    conflict = next(row for row in report["conflicts"] if row["path"] == ".agents/rules/evidence.md")
    assert conflict["reason"] == "unmanaged_local"
    assert conflict["packaged_source"] == "torque/data/rules/evidence.md (Antigravity copy)"
    for added in (".agents/skills/status/SKILL.md", ".agents/skills/torque-help/SKILL.md", ".agents/agents/worker.md"):
        assert (root / added).is_file() and added in manifest(root)


def test_manifest_stays_readable_by_versions_before_the_antigravity_copies(environment):
    root, package = environment
    ws._materialize_workflows(root)
    updates.record_initial_templates(root)
    data = json.loads((root / updates.MANIFEST).read_text(encoding="utf-8"))
    # Those versions accept only these locations under "files" and ignore other sections.
    known = (".claude/commands/", ".claude/rules/", ".claude/skills/", ".claude/agents/", ".agents/skills/")
    assert all(name.startswith(known) for name in data["files"])
    assert set(data["antigravity_files"]) == {".agents/rules/evidence.md", ".agents/agents/worker.md"}
    # One of them rewrites the manifest without the section; the unchanged copies are adopted again.
    del data["antigravity_files"]
    put(root, updates.MANIFEST, json.dumps(data))
    report = updates.update_templates(root)
    assert report["counts"] == {"current": len(data["files"]), "adopt": 2} and not report["conflicts"]
    assert ".agents/rules/evidence.md" in manifest(root)


def test_changed_packaged_rule_updates_an_untouched_copy(environment, monkeypatch):
    root, package = environment
    ws._materialize_workflows(root)
    updates.record_initial_templates(root)
    put(package, "data/rules/evidence.md", RULE + "- Record dates.\n")
    monkeypatch.setattr(updates, "__version__", "test-v2")
    report = updates.update_templates(root)
    row = next(row for row in report["actions"] if row["path"] == ".agents/rules/evidence.md")
    assert row["action"] == "update" and row["applied"]
    assert "- Record dates.\n" in (root / ".agents/rules/evidence.md").read_text(encoding="utf-8")
    assert manifest(root)[".agents/rules/evidence.md"]["version"] == "test-v2"


def test_packaged_bundle_gives_a_complete_surface(tmp_path):
    root = ws.init_workspace(tmp_path / "firm", "Example firm")
    rules = sorted(path.name for path in (root / ".claude/rules").glob("*.md"))
    assert rules and rules == sorted(path.name for path in (root / ".agents/rules").glob("*.md"))
    for name in rules:
        lines = header((root / ".agents/rules" / name).read_text(encoding="utf-8"))
        assert lines[0] == "trigger: always_on" and lines[1].startswith("description: ") and len(lines[1]) > 14
    commands = sorted(path.stem for path in (root / ".claude/commands").glob("*.md"))
    assert {"help", "context", "undo"} <= set(commands)
    for name in commands:
        text = (root / ".agents/skills" / antigravity.skill_name(name) / "SKILL.md").read_text(encoding="utf-8")
        lines = header(text)
        assert lines[0] == "name: " + antigravity.skill_name(name) and lines[1].startswith("description: ")
        assert "$ARGUMENTS" not in text
    for name in antigravity.BUILTIN_COMMANDS:
        assert not (root / ".agents/skills" / name).exists()
    agents = sorted(path.name for path in (root / ".claude/agents").glob("*.md"))
    assert agents and agents == sorted(path.name for path in (root / ".agents/agents").glob("*.md"))
    for name in agents:
        lines = header((root / ".agents/agents" / name).read_text(encoding="utf-8"))
        tools = [line[4:] for line in lines if line.startswith("  - ")]
        assert tools and set(tools) <= KNOWN_TOOLS
        assert lines[-2:] == ["mainAgent: false", "subagent: true"]
    assert (root / ".agents/skills/salesforce-npsp/SKILL.md").is_file()
    check = updates.update_templates(root, check=True)
    assert set(check["counts"]) == {"current"} and not check["conflicts"]
