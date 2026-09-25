import json

import pytest

from torque import permissions as p, workspace as ws
from torque.presence import Presence

YES = lambda: Presence(True, "")


def test_generated_rules_cover_routes():
    gen = p.generate()
    for rule in ("Bash(sf project deploy start:*)", "Bash(sf data update:*)", "Bash(sf apex run:*)",
                 "Bash(torque deploy:*)", "Bash(torque recover:*)", "Bash(python3:*)", "Bash(curl:*)",
                 "mcp__claude-in-chrome"):
        assert rule in gen["ask"]
    for rule in ("Bash(torque approval grant:*)", "Bash(torque launch:*)", "Edit(/clients/*/consent.json)",
                 "Edit(/clients/*/approvals/**)", "Read(~/.config/torque/approval.key)", "Bash(sf alias set:*)"):
        assert rule in gen["deny"]
    assert not any(rule.startswith("Write(") for rule in gen["deny"])
    assert gen["disableBypassPermissionsMode"] == "disable"
    assert "allow" not in gen


def test_merge_keeps_user_rules_and_removes_conflicting_allows():
    settings = {"hooks": {"PreToolUse": []},
                "permissions": {"allow": ["Bash(git status)", "Bash(python3:*)", "Bash(sf project deploy start *)",
                                          "mcp__claude-in-chrome__computer"],
                                "ask": ["Bash(npm test)"], "defaultMode": "bypassPermissions"}}
    merged = p.merge(settings, p.generate())
    assert merged["hooks"] == {"PreToolUse": []}
    assert merged["permissions"]["allow"] == ["Bash(git status)"]
    assert "Bash(npm test)" in merged["permissions"]["ask"]
    assert "defaultMode" not in merged["permissions"]
    assert p.drift(merged, p.generate()) == []


def test_drift_reports_missing_and_allowed():
    problems = p.drift({"permissions": {"allow": ["Bash(sf apex run:*)"]}}, p.generate())
    assert any("missing ask" in x for x in problems) and any("allows" in x for x in problems)
    assert any("bypass" in x for x in problems)
    assert p.drift({}, p.generate())


def test_write_settings_needs_operator_and_merges(tmp_path):
    root = ws.init_workspace(tmp_path / "w", "Firm")
    with pytest.raises(ws.WorkspaceError, match="terminal"):
        p.write_settings(root, presence=lambda: Presence(False, "needs a real terminal"))
    path = p.write_settings(root, presence=YES)
    assert p.drift(json.loads(path.read_text(encoding="utf-8")), p.generate()) == []
    p.write_settings(root, presence=YES)


def test_rule_file_materialized_and_removed(tmp_path):
    root = ws.init_workspace(tmp_path / "w", "Firm")
    rule = root / ".claude" / "rules" / "production-approval.md"
    assert not rule.exists()
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    assert "propose, show the plan and stop" in rule.read_text(encoding="utf-8").lower()
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    ws.set_ai_access(root, "full")
    assert not rule.exists()
    ws.set_ai_access(root, "build-only")
    assert not rule.exists()


def test_new_workspace_does_not_get_the_connected_rule(tmp_path):
    root = ws.init_workspace(tmp_path / "w", "Firm")
    assert not (root / ".claude" / "rules" / "production-approval.md").exists()
    assert "production-approval.md" in (root / ".claude" / "rules" / "delivery-practice.md").read_text(encoding="utf-8")


def _key_rules(home):
    key = f"/{home.as_posix()}/.config/torque/approval.key"
    return f"Read({key})", f"Edit({key})"


@pytest.mark.skipif(__import__("os").name == "nt", reason="the ~ key rule is the POSIX key location")
def test_v2_2_owner_rules_keep_the_absolute_key_rule(tmp_path, monkeypatch):
    """The owner's (non-delegated) output is unchanged from a15: the fixed rules
    plus the absolute key rules for the owner's own home, in that order."""
    monkeypatch.setenv("HOME", str(tmp_path))
    assert p.deny_rules() == p.FIXED_DENY_RULES + _key_rules(tmp_path)
    assert p.generate()["deny"] == list(dict.fromkeys(p.FIXED_DENY_RULES + _key_rules(tmp_path)))


@pytest.mark.skipif(__import__("os").name == "nt", reason="the ~ key rule is the POSIX key location")
def test_v2_2_delegated_rules_name_no_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    deny = p.generate("unattended", delegated=True)["deny"]
    assert not any(str(tmp_path) in r for r in deny)
    assert "Read(~/.config/torque/approval.key)" in deny and "Edit(~/.config/torque/approval.key)" in deny


@pytest.mark.skipif(__import__("os").name == "nt", reason="the ~ key rule is the POSIX key location")
def test_v2_2_drift_accepts_the_home_relative_key_rule_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "agent"))
    relative_only = {"permissions": {"ask": list(p.ASK_RULES), "deny": list(p.FIXED_DENY_RULES),
                                     "disableBypassPermissionsMode": "disable"}}
    assert p.drift(relative_only, p.generate()) == []
    # Without the ~ form, the absolute key rule is still required (fail closed).
    for missing in ("Read(~/.config/torque/approval.key)", "Edit(~/.config/torque/approval.key)"):
        settings = json.loads(json.dumps(relative_only))
        settings["permissions"]["deny"].remove(missing)
        problems = p.drift(settings, p.generate())
        assert f"missing deny rule {missing}" in problems
        absolute = missing.replace("~", "/" + (tmp_path / "agent").as_posix())
        assert f"missing deny rule {absolute}" in problems
    # A key rule for some other account's home does not stand in for this account's.
    other = {"permissions": {**relative_only["permissions"],
                             "deny": [r for r in p.FIXED_DENY_RULES if "approval.key" not in r]
                             + list(_key_rules(tmp_path / "delegate"))}}
    assert any("approval.key" in x for x in p.drift(other, p.generate()))
