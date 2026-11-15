"""Rules and docs match the actual CLI and hosts (audit item N15): the commands they tell a
session or a consultant to run parse as written, recovery recipes use the public
`torque recover` route, Windows readers get commands that work there, and the hosts
table says what each host loads."""
import json
import os
from pathlib import Path
import re
import shlex

import pytest

from torque import cli, consent, gate_connected as gc, workspace as ws
from torque import connected_routes as cr
from torque.presence import Presence

REPO = Path(__file__).resolve().parents[1]
RULE = REPO / "src" / "torque" / "data" / "connected" / "production-approval.md"
YES = lambda: Presence(True, "")
PLACEHOLDERS = {"<snapshot-id>": "0123456789abcdef", "<org-alias>": "acme-sbx", "<private-path>": ".",
                "<client-name>": "acme", "SNAPSHOT": "0123456789abcdef", "PRIVATE": ".", "TEST": "acme",
                "ALIAS": "acme-sbx", "NAME": "acme", "ID": "chg-0123456789ab", "TEXT": "Add Tier", "PATH": ".",
                "W": "."}


def text_of(path):
    return " ".join((REPO / path).read_text(encoding="utf-8").split())


def filled(command):
    words = shlex.split(command)
    return [PLACEHOLDERS.get(word, word) for word in words]


def parses(argv):
    """Torque's own parser accepts these words (what follows `--` is the approved command)."""
    head = argv[:argv.index("--")] if "--" in argv else argv
    try:
        cli.build_parser().parse_args(head)
    except SystemExit as exc:
        return exc.code == 0
    return True


def approval_requests(text):
    return [span for span in re.findall(r"`(torque approval request [^`]*)`", text)]


def test_every_approval_request_the_connected_rule_shows_parses():
    found = approval_requests(text_of(RULE))
    assert len(found) == 2, found
    for command in found:
        assert parses(filled(command)[1:]), command
    browser = next(command for command in found if "--browser" in command)
    assert browser == gc.BROWSER_REQUEST.format(workspace="W", client="NAME", org="ALIAS")


def test_the_browser_window_example_in_the_connected_doc_parses():
    found = [command for command in approval_requests(text_of("docs/connected-approval.md")) if "--browser" in command]
    assert found and all(parses(filled(command)[1:]) for command in found), found


@pytest.fixture
def connected(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    for name in cr.SF_CONTAINER_VARS:
        monkeypatch.delenv(name, raising=False)
    root = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(root, "Acme")
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    from collections import namedtuple
    Org = namedtuple("Org", "org_id_18 detected_org_type is_production instance_url")
    orgs = {"acme-sbx": Org("00D000000000001AAA", "sandbox", False, "https://acme--sbx.sandbox.my.salesforce.com")}
    letter = tmp_path / "a.pdf"
    letter.write_bytes(b"agreement")
    consent.record_consent(root, "Acme", "2026-09-30", letter, ["metadata", "records"], ["acme-sbx"], ["Contact"],
                           presence=YES, resolve=orgs.get)
    consent.sign_off(root, "Acme", "Reviewer", presence=YES)
    return Path(os.path.realpath(root))


@pytest.mark.parametrize("command", ["torque browser run --target-org acme-sbx",
                                     "sf project deploy start -m Flow:Case_Escalation -o acme-sbx"])
def test_the_request_a_refusal_names_parses_as_written(connected, command):
    told = gc.decide_connected("Bash", {"command": command}, connected, connected, env={"TORQUE_CLIENT": "acme"},
                               permission_mode="default", session_id="s1", tool_use_id="t1").reason
    found = approval_requests(told)
    assert len(found) == 1 and "..." not in found[0], told
    assert parses(filled(found[0])[1:]), found[0]
    assert "--client acme" in found[0] and "--org acme-sbx" in found[0]


def test_torque_browser_names_a_window_request_that_parses(connected, monkeypatch):
    from torque import browser_guard as bg
    monkeypatch.setenv("TORQUE_WORKSPACE", str(connected / "clients" / "acme"))
    monkeypatch.setattr("jsc_revert.org_detect.resolve_org", lambda alias, **k: None)
    with pytest.raises(bg.GuardRefused, match="not authenticated"):
        bg.connected_guard("acme-sbx")
    from collections import namedtuple
    Org = namedtuple("Org", "org_id_18 detected_org_type is_production instance_url")
    org = Org("00D000000000001AAA", "sandbox", False, "https://acme--sbx.sandbox.my.salesforce.com")
    monkeypatch.setattr("jsc_revert.org_detect.resolve_org", lambda alias, **k: org)
    with pytest.raises(bg.GuardRefused) as caught:
        bg.connected_guard("acme-sbx")
    found = approval_requests(str(caught.value))
    assert len(found) == 1 and parses(filled(found[0])[1:]) and "--org acme-sbx" in found[0], str(caught.value)


RECOVERY = {"revert-show": "show", "revert-preview": "preview", "revert": "run", "revert-discard": "discard"}
FUNCTIONS = {"show": "cmd_revert_show", "preview": "cmd_revert_preview", "run": "cmd_revert",
             "discard": "cmd_revert_discard"}


def recover_commands(text):
    lines = text.replace("\\\n", " ").splitlines()
    return [line.strip() for line in lines if line.strip().startswith("torque recover ")]


def dispatched(monkeypatch, words):
    """What `torque recover ...` hands the recovery package, parsed by that package's own parser."""
    from jsc_revert import cli as revert_cli
    seen = {}
    monkeypatch.setattr(cli, "_dispatch", lambda route, argv, display=None: seen.update(route=route, argv=argv) or 0)
    assert cli.main(words) == 0
    args, workspace, client = cli._context_options(seen["argv"])
    assert seen["route"] == "revert" and workspace and client, seen
    return revert_cli.build_parser().parse_args(args)


@pytest.mark.parametrize("name,action", sorted(RECOVERY.items()))
def test_recovery_recipes_use_the_public_route_and_it_parses(monkeypatch, name, action):
    text = (REPO / "workflows" / f"{name}.md").read_text(encoding="utf-8")
    assert "torque revert revert" not in text.replace("`torque revert revert show` still works", "")
    commands = recover_commands(text)
    assert len(commands) == 1 and commands[0].startswith(f"torque recover {action} "), commands
    parsed = dispatched(monkeypatch, filled(commands[0])[1:])
    assert parsed.func.__name__ == FUNCTIONS[action] and parsed.org == "acme-sbx"
    assert (REPO / ".claude" / "commands" / f"{name}.md").read_text(encoding="utf-8").count(commands[0]) == 1


def test_the_catalogue_names_the_public_recovery_route():
    entries = {entry["name"]: entry for entry in json.loads((REPO / "workflows" / "catalogue.json").read_text(
        encoding="utf-8"))}
    for name, action in RECOVERY.items():
        assert entries[name]["implementation"] == f"torque recover {action}", name
    assert not [entry["name"] for entry in entries.values() if "revert revert" in entry["implementation"]]


@pytest.mark.parametrize("path", ["docs/live-acceptance.md", "docs/workflow-continuity.md"])
def test_guides_teach_the_public_routes(monkeypatch, path):
    text = (REPO / path).read_text(encoding="utf-8")
    assert "intentionally `torque revert revert show" not in text
    assert "torque revert ... deploy" not in text and "torque recover" in text
    for command in recover_commands(text):
        assert dispatched(monkeypatch, filled(command)[1:]).org == "acme-sbx", command
    for command in re.findall(r"`(torque deploy --workspace [^`]*)`", " ".join(text.split())):
        parsed = dispatched(monkeypatch, filled(command)[1:])
        assert parsed.target_org == "acme-sbx" and parsed.dry_run, command


def test_engagement_commands_in_prose_name_their_workspace():
    text = text_of("docs/engagement-records.md")
    found = re.findall(r"`(torque (?:initiative set-state|engagement list)[^`]*)`", text)
    assert len(found) == 2, found
    for command in found:
        assert parses(filled(command)[1:]), command


def test_windows_install_installs_the_reviewed_checkout_and_names_the_fallback():
    section = text_of("docs/installation.md").split("## Windows", 1)[1].split("## Optional capabilities", 1)[0]
    assert "pip install -e" not in section and r".venv\Scripts\python.exe -m pip install ." in section
    assert "Smart App Control" in section and r".venv\Scripts\python.exe -m torque doctor" in section
    assert "validation-alpha21.md" in section
    assert "validation-alpha10.md" not in text_of("docs/installation.md")


def test_the_upgrade_guide_shows_packaged_text_in_a_form_windows_powershell_runs():
    # Windows PowerShell 5.1 drops a double quote inside a single-quoted argument to a program
    # (checked on 2026-10-28: `print("x")` arrived as `print(x)`). `--diff` replaced the guide's
    # python -c snippet for the packaged text, and no such snippet may come back.
    text = (REPO / "docs" / "workspace-upgrades.md").read_text(encoding="utf-8")
    assert "torque workspace upgrade /path/to/private-workspace --diff .claude/commands/qa.md" in text
    assert not re.search(r"python3? -c '[^']*\"", text)


def test_engagement_records_give_a_powershell_form_of_the_id_capture():
    # The POSIX example captures the ID with $(...) and a single-quoted python3 -c, which
    # Windows PowerShell cannot run; the PowerShell form beside it needs neither.
    text = (REPO / "docs" / "engagement-records.md").read_text(encoding="utf-8")
    block = text.split("```powershell", 1)[1].split("```", 1)[0]
    assert "ConvertFrom-Json).id" in block and "$change_id = (torque change create" in block
    assert "python" not in block and "$(" not in block
    words = filled(block.split("(", 1)[1].split("| ConvertFrom-Json", 1)[0])
    assert parses(words[1:]), words


def test_the_hosts_table_says_what_each_host_loads():
    text = (REPO / "docs" / "hosts.md").read_text(encoding="utf-8")
    table = text.split("## What each host loads", 1)[1].split("\n## ", 1)[0]
    assert "| From the workspace | Claude Code | Antigravity | Codex |" in table
    rows = {line.split("|")[1].strip(): [cell.strip() for cell in line.split("|")[2:-1]]
            for line in table.splitlines() if line.startswith("| ") and "---" not in line}
    assert rows["Instructions"][2].startswith("`AGENTS.md`") and rows["Standing and mode rules"][2].startswith("Not")
    assert rows["Gate (build-only, connected)"][2].startswith("None")
    assert rows["`torque launch`, `torque doctor`"][2] == "No"
    # What comes from OpenAI's documentation says so, with its source.
    assert "From OpenAI's Codex documentation, checked 2026-10-28, not run with Torque" in " ".join(table.split())
    for page in ("agent-configuration/agents-md", "build-skills", "agent-configuration/rules", "docs/hooks"):
        assert f"https://learn.chatgpt.com/" in table and page in table, page
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "| Codex |" in readme and "do not use it in a build-only or connected workspace" in readme
