"""Connected tooling, browser context and data-consent audit regressions."""
import io
from types import SimpleNamespace

import pytest

from torque import approval, browser_guard, changes, consent, gate_connected, permissions, workspace as ws
from torque.connected_routes import classify
from torque.presence import Presence


ORG = SimpleNamespace(org_id_18="00D000000000001AAA", detected_org_type="sandbox",
                      is_production=False, instance_url="https://example.org")
YES = lambda: Presence(True, "")


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    root = ws.init_workspace(tmp_path / "workspace", "Synthetic workspace")
    ws.add_client(root, "Alpha")
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    set_consent(root, ["metadata", "records"])
    return root


def set_consent(root, categories):
    evidence = root / "agreement.txt"
    evidence.write_text("Synthetic agreement", encoding="utf-8")
    consent.record_consent(root, "Alpha", "2026-09-30", evidence, categories, ["alpha-dev"], [],
                           presence=YES, resolve=lambda _: ORG)
    consent.sign_off(root, "Alpha", "reviewer", presence=YES)


def decide(root, tool, arguments, mode="default", cwd=None):
    return gate_connected.decide_connected(tool, arguments, root, cwd or root,
                                           env={"TORQUE_CLIENT": "alpha"}, permission_mode=mode)


@pytest.mark.parametrize("command", [
    "git status", "git commit -m ordinary", "git diff", "hg status", "gh pr list",
    "eslint .", "pylint module.py", "mypy .", "flake8 .", "prettier --check .",
    "black --check .", "isort --check .", "ruff check .", "tsc --noEmit", "shellcheck tool.sh",
    "sf code-analyzer run --workspace project", "sf dev generate command", "sf lightning dev app",
    "rg match .", "ag match .", "ack match .", "fd .", "sort --compress-program=helper input.txt",
    "split --filter=helper input.txt", "sed -f project.sed input.txt", "awk -f project.awk input.txt",
    "gawk -f project.awk input.txt", "git.exe status", "eslint.cmd .",
])
def test_extensible_tooling_needs_review_even_without_inline_configuration(root, command):
    # Configuration/hooks can predate this tool call; no inline payload is necessary.
    cwd = root / "clients" / "alpha"
    assert decide(root, "Bash", {"command": command}, cwd=cwd).action == "ask"
    assert decide(root, "Bash", {"command": command}, mode="bypassPermissions", cwd=cwd).action == "deny"


@pytest.mark.parametrize("profile", ["interactive", "unattended"])
@pytest.mark.parametrize("command", ["git status", "hg status", "gh extension exec tool", "eslint .",
                                     "pylint app.py", "mypy .", "sf code-analyzer run", "rg match .",
                                     "awk -f project.awk input.txt", "git.exe status", "eslint.cmd ."])
def test_permission_backstop_removes_allows_for_extensible_tools(profile, command):
    generated = permissions.generate(profile)
    current = {"permissions": {"allow": [f"Bash({command})", "Bash(echo ready)"]}}
    merged = permissions.merge(current, generated, profile)
    assert merged["permissions"]["allow"] == ["Bash(echo ready)"]


@pytest.mark.parametrize("tool,arguments", [
    ("mcp__claude-in-chrome__read_page", {"tabId": 7}),
    ("mcp__claude-in-chrome__computer", {"action": "screenshot", "tabId": 7}),
    ("mcp__chrome-devtools__take_screenshot", {"pageId": 7, "url": "https://example.org"}),
    ("mcp__playwright__browser_snapshot", {"targetOrg": "alpha-dev"}),
    ("mcp__playwright__browser_navigate", {"url": "https://example.org"}),
    ("mcp__computer-use__screenshot", {}),
    ("mcp__desktop__computer", {"action": "read_page"}),
])
@pytest.mark.parametrize("granted", [False, True])
def test_external_browser_and_desktop_reads_have_no_verified_context(root, tool, arguments, granted):
    if granted:
        browser_window(root)
    decision = decide(root, tool, arguments)
    assert decision.action == "deny"
    assert "context" in decision.reason
    assert classify(tool, arguments)[0].data == "records"


@pytest.mark.parametrize("tool,arguments", [
    ("Bash", {"command": "sf project retrieve start -m Flow:Sample -o alpha-dev"}),
    ("Bash", {"command": "sf sobject describe -s Account -o alpha-dev"}),
    ("Bash", {"command": "sfdx force:source:retrieve -m Flow:Sample -u alpha-dev"}),
    ("Bash", {"command": "sf api request rest /services/data/v60.0/sobjects -o alpha-dev"}),
    ("Bash", {"command": "torque approval request --org alpha-dev --capture-before-metadata Flow:Sample"}),
    ("mcp__salesforce__get_metadata", {"targetOrg": "alpha-dev"}),
    ("Bash", {"command": "sf project deploy validate -m Flow:Sample -o alpha-dev"}),
])
def test_metadata_reads_require_metadata_consent(root, tool, arguments):
    set_consent(root, ["records"])
    decision = decide(root, tool, arguments)
    assert decision.action == "deny" and "metadata" in decision.reason
    set_consent(root, ["metadata"])
    assert decide(root, tool, arguments).action == "allow"


def browser_window(root):
    change = changes.create_change(root, "Alpha", "Browser check", "Inspect the form", [], "alpha-dev")
    request = approval.create_request(root, "Alpha", change["id"], "alpha-dev", browser_minutes=10,
                                      purpose="Inspect the form", resolve=lambda _: ORG)
    approval.grant(root, "Alpha", request["id"], presence=YES, confirm=lambda: True,
                   out=io.StringIO(), resolve=lambda _: ORG)


@pytest.mark.parametrize("categories", [["metadata"], ["records"]])
def test_guarded_browser_requires_both_data_categories_at_gate_and_direct_entry(root, monkeypatch, categories):
    browser_window(root)
    set_consent(root, categories)
    decision = decide(root, "Bash", {"command": "torque browser run --target-org alpha-dev"})
    assert decision.action == "deny" and "consent" in decision.reason
    monkeypatch.setenv("TORQUE_WORKSPACE", str(root / "clients" / "alpha"))
    monkeypatch.setattr(browser_guard, "org_key", lambda _: ("alpha", "sandbox"))
    with pytest.raises(browser_guard.GuardRefused, match="consent"):
        browser_guard.connected_guard("alpha-dev", resolve=lambda _: ORG)


@pytest.mark.parametrize("remaining", [["metadata"], ["records"]])
def test_browser_guard_rechecks_categories_after_start(root, monkeypatch, remaining):
    browser_window(root)
    monkeypatch.setenv("TORQUE_WORKSPACE", str(root / "clients" / "alpha"))
    monkeypatch.setattr(browser_guard, "org_key", lambda _: ("alpha", "sandbox"))
    guard = browser_guard.connected_guard("alpha-dev", resolve=lambda _: ORG)
    assert guard.authorized()
    set_consent(root, remaining)
    assert not guard.authorized()
