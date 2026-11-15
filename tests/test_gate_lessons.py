"""Build-only mode and lessons: `torque lesson for --workspace .` reads the firm's lessons
and those shipped with Torque, so it is allowed; every form that could select a client,
an initiative or any folder other than the workspace itself stays blocked."""
import argparse
import json
import os

import pytest

from torque import cli, connected_routes as routes, gate, workspace as ws

ALLOWED = [
    "torque lesson for flow deploy --workspace .",
    "torque lesson for --type Flow --task deploy --workspace . --json",
    "torque lesson recall --workspace=. --limit 3",
    'torque lesson for --command "sf project deploy start --metadata Flow:Gift_Entry" --workspace .',
    "torque lesson for --error \"No such column 'X__c' on entity 'Account'\" --workspace .",
    # Help prints the options and reads no lesson.
    "torque lesson --help",
    "torque lesson -h",
    "torque lesson for --help",
    "torque lesson recall -h",
    "python -m torque lesson for --help",
    "python -m torque lesson for field access --workspace .",
    "cd project && torque lesson for --type Flow --workspace ..",
    "torque lesson for flow --workspace ./",
    "env -C . torque lesson for flow --workspace .",
]
BLOCKED = [
    "torque lesson for flow",                                              # no workspace named: a selection could be inherited
    # Candidates are the firm's lessons nobody has reviewed yet; they are not shown in build-only mode.
    "torque lesson for flow --workspace . --include-candidates",
    "torque lesson for --error x --include-candidates --workspace .",
    # Help only in exactly these forms.
    "torque lesson list --help",
    "torque lesson add --help",
    "torque lesson for --help --client acme",
    "torque lesson --client acme --help",
    "torque lesson for flow --workspace . --client acme",
    "torque lesson for flow --workspace . --client=acme",
    "torque lesson for flow --workspace . --initiative playbook",
    "torque lesson for flow --client acme",
    "torque lesson for flow --workspace clients/acme",
    "torque lesson for flow --workspace=clients/acme",
    "torque lesson for flow --workspace ./clients/../clients/acme",
    "torque lesson for flow --workspace . --workspace clients/acme",
    "torque lesson for flow --workspace $W",
    "torque lesson for flow --workspace ~/ws",
    "torque lesson for flow --workspace .claude/worktrees/copy",
    "torque lesson for flow --workspace ..",                               # the folder above is not this workspace
    "cd .. && torque lesson for flow --workspace .",
    "cd clients/acme && torque lesson for flow --workspace .",
    "env -C clients/acme torque lesson for flow --workspace .",
    "torque lesson for flow --workspace . --path clients/acme/force-app",
    "torque lesson for flow --workspace . --path .",
    "torque lesson for flow --workspace . --clie acme",
    "torque lesson for --command '--client acme' --workspace .",
    "torque lesson for --command 'sf x --client acme' --workspace .",    # also when the shell would split it differently
    "torque lesson for --command 'x --workspace clients/acme' --workspace .",
    "torque lesson for flow --workspace . -- --client acme",
    "torque lesson --workspace . for flow",
    "torque lesson --client acme for flow --workspace .",
    "torque lesson list --workspace .",
    "torque lesson show L-20261114-a3f2 --workspace .",
    "torque lesson add 'a lesson' --workspace . --scope firm",
    "torque lesson add 'a lesson' --workspace . --from-file clients/acme/context.md",
    "torque lesson harvest --host claude --workspace .",
    "torque lesson review --workspace .",
    "torque lesson",
    "torque lesson for",
    "python -m torque lesson for flow --workspace . --client acme",
    "TORQUE_WORKSPACE=clients/acme torque lesson for flow",
    "jsc-memory for flow --workspace .",
    "python -m jsc_memory.cli for flow --workspace .",
]
# Connected mode decides by the client a command names; each of these is local work of that client.
CONNECTED = [
    "torque lesson for flow deploy",
    'torque lesson for --type Flow --task deploy --command "sf project deploy start --metadata Flow:Gift_Entry" --json',
    "torque lesson for --error \"No such column 'X__c' on entity 'Account'\" --path force-app --limit 3",
    'torque lesson add --when "you deploy a flow" --do "read it back" --because "it can stay inactive" '
    "--source consultant --evidence reproduced:S-1 --type Flow --task deploy --keyword activation --error INVALID_FIELD",
    'torque lesson add "When: you deploy a flow. Do: read it back." --scope firm --kind practice --title "Read it back"',
    "torque lesson add --from-file notes/flows.md --when a --do b --because c --body-file notes/body.md --session S-1",
    'torque lesson helpful L-20250303-a3f2 --note "saved a failed deploy" --session S-1',
    'torque lesson stale L-20250303-a3f2 --reason "the platform changed" --superseded-by L-20250303-b111',
    "torque lesson verify L-20250303-a3f2 --evidence consultant-said",
    'torque lesson retire L-20250303-a3f2 --reason "no longer true"',
    "torque lesson restore L-20250303-a3f2",
    "torque lesson edit L-20250303-a3f2 --set applies.types=Flow",
    "torque lesson review",
    "torque lesson promote L-20250303-a3f2 --to rule --name flows",
    "torque lesson promote --apply promote-L-20250303-a3f2-to-firm.md",
    "torque lesson harvest --host claude --dir notes --since 2026-01-01",
    "torque lesson stats --since 2026-01-01",
    "torque lesson list --state candidate --scope client --task deploy --type Flow",
    "torque lesson show L-20250303-a3f2",
    "torque lesson search flow activation",
    "torque lesson migrate",
    'torque session add --summary "Built the flow" --status executed --learned "When: you deploy a flow. Do: read '
    'it back." --learned-from consultant --learned-scope firm',
]


@pytest.fixture
def root(tmp_path, monkeypatch):
    for name in ("TORQUE_WORKSPACE", "JSC_ROOT", "TORQUE_CLIENT"):
        monkeypatch.delenv(name, raising=False)
    root = ws.init_workspace(tmp_path / "workspace", "Synthetic workspace")
    ws.add_client(root, "Acme")
    (root / "project").mkdir()
    return root


@pytest.mark.parametrize("command", ALLOWED)
def test_lesson_for_with_the_workspace_named_is_allowed_in_build_only(root, command):
    assert gate.decide("Bash", {"command": command}, root, "build-only", cwd=root) == (True, ""), command


@pytest.mark.parametrize("command", BLOCKED)
def test_every_other_lesson_form_stays_blocked_in_build_only(root, command):
    allowed, reason = gate.decide("Bash", {"command": command}, root, "build-only", cwd=root)
    assert not allowed and reason, command


def test_the_block_says_which_form_is_allowed(root):
    _, reason = gate.decide("Bash", {"command": "torque lesson for flow --workspace . --client acme"}, root,
                            "build-only", cwd=root)
    assert "torque lesson for [WORDS] --workspace ." in reason and "never a client's" in reason


def test_a_worktree_copy_of_the_workspace_does_not_change_the_answer(root):
    copy = root / ".claude" / "worktrees" / "copy"
    (copy / "clients" / "acme").mkdir(parents=True)
    decide = lambda command: gate.decide("Bash", {"command": command}, root, "build-only", cwd=root)[0]
    for command in ALLOWED:
        assert decide(command), command
    assert not decide("torque lesson for flow --workspace .claude/worktrees/copy")
    assert not decide("torque lesson for flow --workspace .claude/worktrees/copy/clients/acme")
    assert not decide("cd .claude/worktrees/copy && torque lesson for flow --workspace .")


def test_a_folder_outside_the_workspace_is_never_the_named_workspace(root, tmp_path, capsys, monkeypatch):
    # Torque reads a folder that holds a client.json as that client, wherever the folder is.
    other = ws.init_workspace(tmp_path / "other", "Another synthetic workspace")
    ws.add_client(other, "Zenith")
    theirs = other / "clients" / "zenith"
    assert cli.main(["lesson", "add", "When: you deploy a flow for Zenith. Do: read it back.", "--source",
                     "consultant", "--workspace", str(other), "--client", "Zenith"]) == 0
    capsys.readouterr()
    assert cli.main(["lesson", "for", "--type", "Flow", "--workspace", str(theirs), "--limit", "20", "--json"]) == 0
    assert "client" in {item["scope"] for item in json.loads(capsys.readouterr().out)["lessons"]}
    decide = lambda command: gate.decide("Bash", {"command": command}, root, "build-only", cwd=root)[0]
    for folder in (theirs, other, tmp_path):
        assert not decide(f"torque lesson for --type Flow --workspace {folder.as_posix()}"), folder
        assert not decide(f'torque lesson for --type Flow --workspace "{folder}"'), folder
        assert not decide(f"cd {folder.as_posix()} && torque lesson for --type Flow --workspace ."), folder
    assert decide(f"torque lesson for --type Flow --workspace {root.as_posix()}")


def test_the_allowed_form_reads_the_firm_and_the_platform_and_no_client(root, capsys, monkeypatch):
    client = ["--workspace", str(root), "--client", "Acme"]
    assert cli.main(["lesson", "add", "When: you deploy a flow for this client. Do: read it back.", "--source",
                     "consultant", *client]) == 0
    assert cli.main(["lesson", "add", "When: you deploy any flow. Do: check that it is active afterwards.", "--source",
                     "consultant", "--scope", "firm", *client]) == 0
    ws.set_ai_access(root, "build-only")
    capsys.readouterr()
    # Even with a client selected in the environment, the named workspace decides.
    monkeypatch.setenv("TORQUE_WORKSPACE", str(root / "clients" / "acme"))
    monkeypatch.chdir(root)
    command = ["lesson", "for", "--type", "Flow", "--workspace", ".", "--limit", "20", "--json"]
    assert gate.decide("Bash", {"command": "torque " + " ".join(command)}, root, "build-only", cwd=root)[0]
    assert cli.main(command) == 0
    answer = json.loads(capsys.readouterr().out)
    scopes = {item["scope"] for item in answer["lessons"]}
    assert scopes == {"firm", "platform"} and answer["scopes"] == "firm + platform"
    assert "for this client" not in json.dumps(answer)
    assert not (root / "clients" / "acme" / ".torque" / "lessons" / "events.jsonl").exists()
    assert os.path.isfile(root / ".torque" / "lessons" / "events.jsonl")


@pytest.mark.parametrize("command", CONNECTED)
def test_connected_mode_reads_a_lesson_command_as_local_work_of_the_client_it_names(command):
    found = routes.classify("Bash", {"command": command + " --workspace . --client acme"})
    assert [(route.kind, route.org, route.client) for route in found] == [("local", None, "acme")], command


def test_no_lesson_option_reads_as_a_deciding_option_cut_short():
    # `--from` would be `--from-org` cut short, which the gates refuse for every Torque command.
    from jsc_memory.cli import build_parser
    names, parsers = set(), [build_parser()]
    while parsers:
        parser = parsers.pop()
        for action in parser._actions:
            names.update(name for name in action.option_strings if name.startswith("--"))
            if isinstance(action, argparse._SubParsersAction):
                parsers.extend(action.choices.values())
    assert len(names) > 25 and "--source" in names and "--error" in names
    assert [name for name in sorted(names) if routes._cut_short([name])] == []
    assert routes._cut_short(["--from"]) == "--from"
