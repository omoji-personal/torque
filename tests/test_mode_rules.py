"""The rule file a build-only or connected workspace adds to each host's playbook:
written and removed with the mode, and kept up to date by `workspace upgrade` the
way the other packaged files are."""
from importlib import resources
import json
import re

import pytest

from torque import gate, template_updates as updates, workspace as ws
from torque.presence import Presence

YES = lambda: Presence(True, "")
CLAUDE_BUILD = ".claude/rules/build-only.md"
AGY_BUILD = ".agents/rules/build-only.md"
CLAUDE_CONNECTED = ".claude/rules/production-approval.md"
AGY_CONNECTED = ".agents/rules/production-approval.md"
ALL = (CLAUDE_BUILD, AGY_BUILD, CLAUDE_CONNECTED, AGY_CONNECTED)


@pytest.fixture
def root(tmp_path):
    return ws.init_workspace(tmp_path / "firm", "Example firm")


def packaged(*parts):
    return resources.files("torque").joinpath("data", *parts).read_text(encoding="utf-8")


def present(root):
    return {name for name in ALL if (root / name).is_file()}


def sections(root):
    return json.loads((root / updates.MANIFEST).read_text(encoding="utf-8"))


def manifest(root):
    """Every tracked path, from both sections of the manifest."""
    data = sections(root)
    return {**data.get("antigravity_files", {}), **data["files"]}


def header(text):
    match = re.match(r"---\n(.*?)\n---\n\n", text, re.S)
    assert match, text[:80]
    return match.group(1).splitlines()


def by_hand(root, **change):
    """The mode changed in workspace.json itself, as a version without these rules left it."""
    path = root / "workspace.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config.update(change)
    path.write_text(json.dumps(config), encoding="utf-8")


def rows(report):
    return {row["path"]: row for row in report["actions"]}


def test_a_new_workspace_has_no_mode_rule_and_the_same_rules_for_both_hosts(root):
    assert present(root) == set() and not set(manifest(root)) & set(ALL)
    names = sorted(path.name for path in (root / ".claude/rules").glob("*.md"))
    assert names == sorted(path.name for path in (root / ".agents/rules").glob("*.md"))
    assert set(updates.update_templates(root, check=True)["counts"]) == {"current"}


def test_build_only_writes_its_rule_for_both_hosts_and_leaving_removes_it(root):
    ws.set_ai_access(root, "build-only")
    assert present(root) == {CLAUDE_BUILD, AGY_BUILD}
    text = packaged("build-only.md")
    assert (root / CLAUDE_BUILD).read_text(encoding="utf-8") == text
    copy = (root / AGY_BUILD).read_text(encoding="utf-8")
    assert header(copy) == ["trigger: always_on", "description: Build-only mode"] and copy.endswith(text)
    ws.set_ai_access(root, "build-only")
    assert present(root) == {CLAUDE_BUILD, AGY_BUILD}
    ws.set_ai_access(root, "full")
    assert present(root) == set()


def test_connected_writes_the_production_rule_for_both_hosts(root):
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    assert present(root) == {CLAUDE_CONNECTED, AGY_CONNECTED}
    text = packaged("connected", "production-approval.md")
    assert (root / CLAUDE_CONNECTED).read_text(encoding="utf-8") == text
    copy = (root / AGY_CONNECTED).read_text(encoding="utf-8")
    lines = header(copy)
    assert lines[0] == "trigger: always_on" and lines[1].startswith("description: ") and len(lines[1]) > 14
    assert copy.endswith(text)
    ws.set_ai_access(root, "build-only", presence=YES)
    assert present(root) == {CLAUDE_BUILD, AGY_BUILD}
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    ws.set_ai_access(root, "full", presence=YES)
    assert present(root) == set()


def test_setting_a_mode_again_writes_the_packaged_rule_over_an_edited_one(root):
    """As it always did for Claude Code's connected rule: the owner set the mode."""
    ws.set_ai_access(root, "build-only")
    for name in (CLAUDE_BUILD, AGY_BUILD):
        (root / name).write_text("edited\n", encoding="utf-8")
    ws.set_ai_access(root, "build-only")
    assert (root / CLAUDE_BUILD).read_text(encoding="utf-8") == packaged("build-only.md")
    assert header((root / AGY_BUILD).read_text(encoding="utf-8"))[0] == "trigger: always_on"


def test_the_switch_records_its_rules_and_forgets_the_ones_it_removed(root):
    ws.set_ai_access(root, "build-only")
    data = sections(root)
    # Each in the section a version before the Antigravity copies can read.
    assert CLAUDE_BUILD in data["files"] and AGY_BUILD in data["antigravity_files"]
    check = updates.update_templates(root, check=True)
    assert set(check["counts"]) == {"current"} and not check["conflicts"]
    ws.set_ai_access(root, "full")
    assert not set(manifest(root)) & set(ALL)
    assert set(updates.update_templates(root, check=True)["counts"]) == {"current"}


def test_a_workspace_without_a_manifest_gets_its_rules_and_no_manifest(tmp_path):
    root = tmp_path / "bare"
    (root / "clients").mkdir(parents=True)
    (root / "workspace.json").write_text(json.dumps(
        {"schema": "torque.workspace/1", "name": "Synthetic", "profile": "generic"}), encoding="utf-8")
    ws.set_ai_access(root, "build-only")
    assert present(root) == {CLAUDE_BUILD, AGY_BUILD} and not (root / ".torque").exists()


def test_a_damaged_manifest_does_not_stop_the_mode_from_being_set(root):
    (root / updates.MANIFEST).write_text("not json", encoding="utf-8")
    ws.set_ai_access(root, "build-only")
    assert ws.access_mode(ws.load_workspace(root)[1]) == "build-only" and present(root) == {CLAUDE_BUILD, AGY_BUILD}
    with pytest.raises(ws.WorkspaceError, match="invalid"):
        updates.update_templates(root)


def test_claude_codes_connected_rule_is_not_tracked_by_the_upgrade(root):
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    assert AGY_CONNECTED in manifest(root) and CLAUDE_CONNECTED not in manifest(root)
    (root / CLAUDE_CONNECTED).write_text("edited\n", encoding="utf-8")
    report = updates.update_templates(root)
    assert CLAUDE_CONNECTED not in rows(report) and not report["conflicts"]
    assert (root / CLAUDE_CONNECTED).read_text(encoding="utf-8") == "edited\n"


@pytest.mark.parametrize("change,added", [({"ai_access": "build-only"}, {CLAUDE_BUILD, AGY_BUILD}),
                                          ({"ai_access": "connected", "approval": "required"}, {AGY_CONNECTED}),
                                          # What the gate reads as build-only gets the build-only rule.
                                          ({"ai_access": "conected"}, {CLAUDE_BUILD, AGY_BUILD})])
def test_upgrade_adds_the_rules_to_a_workspace_already_in_the_mode(root, change, added):
    by_hand(root, **change)
    check = updates.update_templates(root, check=True)
    assert {path for path, row in rows(check).items() if row["action"] == "add"} == added
    assert not any(row["applied"] for row in check["actions"]) and not check["manifest_updated"]
    assert present(root) == set()
    report = updates.update_templates(root)
    assert present(root) == added and added <= set(manifest(root))
    assert all(rows(report)[path]["applied"] for path in added)
    assert set(updates.update_templates(root, check=True)["counts"]) == {"current"}


def test_upgrade_keeps_an_edited_rule_and_reports_it(root):
    ws.set_ai_access(root, "build-only")
    recorded = manifest(root)[AGY_BUILD]
    (root / AGY_BUILD).write_text("---\ntrigger: always_on\ndescription: Mine\n---\n\nLocal.\n", encoding="utf-8")
    for check in (True, False):
        report = updates.update_templates(root, check=check)
        conflict = next(row for row in report["conflicts"] if row["path"] == AGY_BUILD)
        assert conflict["reason"] == "modified_local" and conflict["suggestion"]
        assert conflict["packaged_source"] == "torque/data/build-only.md (Antigravity copy)"
        assert (root / AGY_BUILD).read_text(encoding="utf-8").endswith("Local.\n")
    assert manifest(root)[AGY_BUILD] == recorded
    assert rows(report)[CLAUDE_BUILD]["packaged_source"] == "torque/data/build-only.md"


def test_upgrade_updates_an_untouched_rule_when_the_packaged_text_changes(root, monkeypatch):
    ws.set_ai_access(root, "build-only")
    real = ws.mode_rules
    monkeypatch.setattr(ws, "mode_rules", lambda mode, data=None: {
        path: (text + "- One more line.\n", source) for path, (text, source) in real(mode, data).items()})
    monkeypatch.setattr(updates, "__version__", "test-next")
    report = updates.update_templates(root)
    for name in (CLAUDE_BUILD, AGY_BUILD):
        assert rows(report)[name]["action"] == "update" and rows(report)[name]["applied"]
        assert (root / name).read_text(encoding="utf-8").endswith("- One more line.\n")
        assert manifest(root)[name]["version"] == "test-next"


def test_a_rule_left_from_another_mode_is_reported_and_left_in_place(root):
    ws.set_ai_access(root, "build-only")
    by_hand(root, ai_access="full")
    for check in (True, False):
        report = updates.update_templates(root, check=check)
        for name in (CLAUDE_BUILD, AGY_BUILD):
            assert rows(report)[name]["action"] == "retired" and not rows(report)[name]["applied"]
            assert "mode the workspace is not in" in rows(report)[name]["suggestion"]
    assert present(root) == {CLAUDE_BUILD, AGY_BUILD}
    # Setting the mode with the command clears them and their record.
    ws.set_ai_access(root, "full")
    assert present(root) == set() and not set(manifest(root)) & set(ALL)


def test_the_packaged_rule_texts_are_in_the_list_the_wheel_is_built_from():
    """A rule text the wheel left out would make `workspace ai-access` fail once installed."""
    import fnmatch
    from pathlib import Path
    project = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    listed = re.findall(r'"([^"]+)"', re.search(r"^torque = \[(.*)\]$", project, re.M).group(1))

    def packaged_by(name):
        # A package-data pattern does not cross a folder.
        return any(len(pattern.split("/")) == len(name.split("/")) and fnmatch.fnmatch(name, pattern)
                   for pattern in listed)
    for _name, parts in ws.MODE_RULES.values():
        assert packaged_by("data/" + "/".join(parts)), parts
    assert not packaged_by("data/modes/build-only.md")


def test_the_rule_paths_are_the_three_new_files():
    assert ws.mode_rule_paths() == {CLAUDE_BUILD, AGY_BUILD, AGY_CONNECTED}
    assert set(ws.mode_rules("build-only")) == {CLAUDE_BUILD, AGY_BUILD}
    assert set(ws.mode_rules("connected")) == {AGY_CONNECTED}
    assert ws.mode_rules("full") == {} and ws.mode_rules("anything else") == {}


@pytest.mark.parametrize("config,mode", [({}, "full"), ({"ai_access": "full"}, "full"),
                                         ({"ai_access": "build-only"}, "build-only"),
                                         ({"ai_access": "connected", "approval": "required"}, "connected"),
                                         ({"ai_access": "connected"}, "build-only"), ({"ai_access": None}, "build-only"),
                                         ({"ai_access": "Full"}, "build-only")])
def test_the_mode_is_the_one_the_gate_enforces(config, mode):
    assert ws.access_mode(config) == mode


def test_the_build_only_rule_is_short_and_says_what_the_gate_enforces(root):
    text = packaged("build-only.md")
    assert len(text.splitlines()) < 25 and chr(0x2014) not in text
    for said in ("No org access", "Client context is off limits", "not to be worked around",
                 "run the command themselves", "torque workspace ai-access"):
        assert said in text, said
    ws.set_ai_access(root, "build-only")

    def allowed(tool, **tool_input):
        return gate.decide(tool, tool_input, root, "build-only", root)[0]
    # No org access, with an alias or the default org; local commands still work.
    assert not allowed("Bash", command="sf data query -q x -o example")
    assert not allowed("Bash", command="sf org display")
    assert not allowed("mcp__salesforce__run_soql_query", query="x")
    for command in ("sf apex generate class -n Example", "sf help", "sf --version"):
        assert allowed("Bash", command=command), command
    # Client context is off limits; the three torque commands it names pass.
    assert not allowed("Read", file_path=str(root / "clients" / "acme" / "notes.md"))
    assert not allowed("Grep", pattern="x", path=str(root / "clients"))
    assert not allowed("Bash", command="torque context --workspace . --client acme")
    assert not allowed("Bash", command="torque doctor --workspace . --client acme")
    for command in ("torque demo demo-folder", "torque workflows list", "torque doctor --workspace ."):
        assert allowed("Bash", command=command), command
    # workspace.json and the hook configuration stay with the owner.
    for name in ("workspace.json", ".claude/settings.json", ".claude/settings.local.json", ".agents/hooks.json"):
        assert not allowed("Write", file_path=str(root / name), content="{}"), name
    # Drafting outside clients/ still works.
    assert allowed("Write", file_path=str(root / "project" / "notes.md"), content="x")


def test_delivery_practice_yields_to_the_rule_of_the_mode(root):
    for text in (packaged("rules", "delivery-practice.md"),
                 (root / ".claude/rules/delivery-practice.md").read_text(encoding="utf-8")):
        assert "In a build-only or connected workspace, the mode's own rule file" in text
        assert "`build-only.md`" in text and "`production-approval.md`" in text and "takes precedence" in text
    assert "takes precedence" in (root / ".agents/rules/delivery-practice.md").read_text(encoding="utf-8")
