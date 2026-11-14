"""The floor: the block of instructions in the workspace AGENTS.md that every host loads at
the start of every session. One source (floor/AGENTS-floor.md), written by `workspace init`,
added or replaced between its markers by `workspace upgrade` (never a byte outside them),
reported by `doctor`; at most 60 lines; every skill it names exists and every command it
shows parses."""
import contextlib
import hashlib
from importlib import resources
import io
import json
from pathlib import Path
import re
import shlex

import pytest

from torque import cli, workspace as ws
from torque import template_updates as updates

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "floor" / "AGENTS-floor.md"
MAX_LINES = 60
# Files whose `torque ...` lines must parse as written (the floor and the newcomer's page).
COMMAND_FILES = ("floor/AGENTS-floor.md", "docs/getting-started.md")
# Placeholder words in those lines and a value that parses in their place.
VALUES = {"NAME": "acme", "SLUG": "acme", "TEXT": "Add Tier", "ID": "L-20261114-ab12", "TASK": "deploy",
          "ALIAS": "acme-sbx", "PROJECT": "force-app", "PATH": ".", "COMMAND": "context"}


def invoke(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


def between_markers(text):
    match = re.search(r"^<!-- torque:floor:begin (\d+) -->\n(.*?)^<!-- torque:floor:end -->$", text, re.S | re.M)
    assert match, "no floor markers"
    return int(match.group(1)), match.group(2)


@pytest.fixture
def firm(tmp_path):
    return ws.init_workspace(tmp_path / "firm", "Example firm")


# --- the block itself ---------------------------------------------------------------------

def test_the_block_fits_in_sixty_lines():
    version, body = between_markers(SOURCE.read_text(encoding="utf-8"))
    lines = body.splitlines()
    assert version >= 1
    assert len(lines) <= MAX_LINES, f"the floor block is {len(lines)} lines; the limit is {MAX_LINES}"
    assert all(len(line) <= 300 for line in lines), [line for line in lines if len(line) > 300]


def test_the_packaged_block_is_the_source():
    packaged = resources.files("torque").joinpath("data", "floor", "AGENTS-floor.md").read_bytes()
    assert packaged == SOURCE.read_bytes(), "run python scripts/sync-workflows.py"
    block, version = ws.floor_block()
    assert block.startswith(f"<!-- torque:floor:begin {version} -->\n") and block.endswith("<!-- torque:floor:end -->\n")


def test_every_skill_the_routing_index_names_exists_at_that_path_in_a_workspace(firm):
    # A host without a skill mechanism (Codex as observed) follows a file path, not a skill
    # name, so each routing line names the file, and the file is where init puts it.
    text = SOURCE.read_text(encoding="utf-8")
    named = re.findall(r"`(\.agents/skills/([a-z0-9-]+)/SKILL\.md)`", text)
    assert len({name for _, name in named}) >= 8, named
    shipped = {path.name for path in (REPO / ".agents" / "skills").iterdir() if (path / "SKILL.md").is_file()}
    assert {name for _, name in named} <= shipped, sorted({name for _, name in named} - shipped)
    for relative, _ in named:
        assert (firm / relative).is_file(), relative
    assert "read skill `" not in text, "name the file to read, not only the skill"


def test_the_block_has_no_em_dash_and_names_no_private_place():
    text = SOURCE.read_text(encoding="utf-8")
    assert "—" not in text and "–" not in text
    assert not re.search(r"[A-Za-z]:\\|/Users/|/home/", text)


# --- every command line parses --------------------------------------------------------------

def torque_lines(text):
    """Each `torque ...` command in backticks or on its own line in a code block."""
    found = [span for span in re.findall(r"`(torque [^`]+)`", text)]
    fenced = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            fenced = not fenced
        elif fenced and line.strip().startswith("torque "):
            found.append(line.strip())
    return found


def words_of(command):
    words = []
    for word in shlex.split(command):
        if "|" in word and not word.startswith("-"):
            word = word.split("|", 1)[0]  # prepared|executed|verified|incomplete: one of them
        words.append(VALUES.get(word, word))
    return words


def parse_error(words):
    """None when Torque's parsers accept these words as a command; otherwise what failed."""
    head = words[1:]
    if not head:
        return "no command"
    route = head[0]
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            if route in cli.DELEGATES or route in cli.PUBLIC_ROUTES:
                rest, _ = cli._scope_options(head[1:], ("--workspace", "--client", "--initiative"))
                package, prefix = (cli.DELEGATES[route], []) if route in cli.DELEGATES else \
                    (cli.DELEGATES[cli.PUBLIC_ROUTES[route][0]], cli.PUBLIC_ROUTES[route][1])
                module = __import__(package, fromlist=["build_parser"])
                module.build_parser().parse_args(prefix + rest)
            else:
                cli.build_parser().parse_args(head)
    except SystemExit as exc:
        return None if exc.code == 0 else (err.getvalue().strip().splitlines() or ["exit"])[-1]
    return None


def subcommands(parser):
    for action in parser._actions:
        if isinstance(action, __import__("argparse")._SubParsersAction):
            return action.choices
    return {}


def test_every_torque_subcommand_the_block_names_exists():
    top = subcommands(cli.build_parser())
    for command in torque_lines(SOURCE.read_text(encoding="utf-8")):
        words = words_of(command)[1:]
        assert words[0] in top, command
        if words[0] in cli.DELEGATES:
            module = __import__(cli.DELEGATES[words[0]], fromlist=["build_parser"])
            assert words[1] in subcommands(module.build_parser()), command
        elif subcommands(top[words[0]]):
            assert words[1] in subcommands(top[words[0]]), command


def test_the_parse_check_catches_a_wrong_command():
    assert parse_error(words_of("torque lesson for --eror X")) is not None
    assert parse_error(words_of("torque check metadta PROJECT")) is not None
    assert parse_error(words_of("torque session add --workspace . --client NAME --summary TEXT --status done")) \
        is not None
    assert parse_error(words_of("torque lesson for --error X --workspace . --client NAME")) is None


@pytest.mark.parametrize("relative", COMMAND_FILES)
def test_every_command_line_parses(relative):
    text = (REPO / relative).read_text(encoding="utf-8")
    commands = torque_lines(text)
    assert len(commands) >= 10, commands
    failures = {command: parse_error(words_of(command)) for command in commands}
    assert not {k: v for k, v in failures.items() if v}, {k: v for k, v in failures.items() if v}


# --- init, upgrade, doctor -----------------------------------------------------------------------

def test_init_writes_the_block_and_claude_md_imports_it(firm):
    text = (firm / "AGENTS.md").read_text(encoding="utf-8")
    version, _ = between_markers(text)
    assert version == ws.floor_block()[1]
    assert text.startswith(ws.AGENT_NOTES_HEAD)
    assert "@AGENTS.md" in (firm / "CLAUDE.md").read_text(encoding="utf-8").splitlines()
    report = updates.update_templates(firm, check=True)
    assert report["floor"]["status"] == "current" and not report["floor"]["applied"]


def test_upgrade_appends_to_a_file_without_markers_and_keeps_its_bytes(firm):
    own = "﻿# Firm rules\r\n\r\nUse the firm's naming scheme.\r\nNo trailing newline here"
    (firm / "AGENTS.md").write_bytes(own.encode("utf-8"))
    check = updates.update_templates(firm, check=True)
    assert check["floor"]["status"] == "appended" and not check["floor"]["applied"]
    assert (firm / "AGENTS.md").read_bytes() == own.encode("utf-8"), "--check wrote"
    code, out, _ = invoke("workspace", "upgrade", str(firm))
    assert code == 0
    assert [line for line in out.splitlines() if "AGENTS.md" in line] == [
        "Appended: the Torque block (version 1) at the end of AGENTS.md, which had none; your text above it "
        "is unchanged."]
    raw = (firm / "AGENTS.md").read_bytes()
    assert raw.startswith(own.encode("utf-8") + b"\r\n\r\n<!-- torque:floor:begin ")
    assert b"\n" not in raw.replace(b"\r\n", b""), "the block takes the file's line endings"
    again = updates.update_templates(firm)
    assert again["floor"]["status"] == "current" and (firm / "AGENTS.md").read_bytes() == raw


def test_upgrade_replaces_an_older_block_and_nothing_outside_it(firm):
    block, version = ws.floor_block()
    before, after = "# Firm\n\nAbove the block.\n\n", "\n## Firm appendix\n\nBelow the block, no newline"
    old = f"<!-- torque:floor:begin {version - 1 or 0} -->\nAn old rule.\n<!-- torque:floor:end -->"
    (firm / "AGENTS.md").write_text(before + old + after, encoding="utf-8", newline="")
    code, out, _ = invoke("workspace", "upgrade", str(firm), "--json")
    row = json.loads(out)["floor"]
    assert code == 0 and row["status"] == "replaced" and row["applied"] and row["previous_version"] == version - 1
    text = (firm / "AGENTS.md").read_text(encoding="utf-8")
    assert text == before + block.rstrip("\n") + after


def test_upgrade_replaces_edits_inside_the_block_and_says_so(firm):
    text = (firm / "AGENTS.md").read_text(encoding="utf-8")
    edited = text.replace("### Standing rules\n", "### Standing rules\n- A rule typed inside the block.\n")
    (firm / "AGENTS.md").write_text(edited, encoding="utf-8")
    code, out, _ = invoke("workspace", "upgrade", str(firm))
    assert any(line.startswith("Replaced: edits inside the Torque block") for line in out.splitlines()), out
    assert (firm / "AGENTS.md").read_text(encoding="utf-8") == text


def test_upgrade_leaves_damaged_markers_alone(firm):
    damaged = "# Firm\n\n<!-- torque:floor:begin 1 -->\nhalf a block\n"
    (firm / "AGENTS.md").write_text(damaged, encoding="utf-8")
    code, out, _ = invoke("workspace", "upgrade", str(firm))
    assert code == 0 and "Not changed: AGENTS.md has a Torque begin or end marker without its pair" in out
    assert (firm / "AGENTS.md").read_text(encoding="utf-8") == damaged
    code, out, _ = invoke("doctor", "--workspace", str(firm))
    assert "Torque block in AGENTS.md: DAMAGED markers" in out


def test_upgrade_replaces_the_unchanged_text_an_earlier_init_wrote(firm, monkeypatch):
    legacy = ("# Private Torque workspace\n\nThis directory holds private employer and client work. Begin by "
              "reading profile.md.")
    # The digest set is what init wrote at each release; a text that hashes to one is replaced whole.
    digest = hashlib.sha256(legacy.encode()).hexdigest()
    monkeypatch.setattr(ws, "LEGACY_AGENT_NOTES_SHA256", ws.LEGACY_AGENT_NOTES_SHA256 | {digest})
    (firm / "AGENTS.md").write_text(legacy, encoding="utf-8")
    report = updates.update_templates(firm)
    assert report["floor"]["status"] == "legacy" and report["floor"]["applied"]
    assert (firm / "AGENTS.md").read_text(encoding="utf-8") == ws.agent_notes()
    (firm / "AGENTS.md").write_text(legacy + "\nA firm line.\n", encoding="utf-8")
    assert updates.update_templates(firm)["floor"]["status"] == "appended"


def test_the_legacy_digests_are_the_texts_earlier_releases_wrote():
    # The digest of this release's init text must not be in the set: it would be replaced every time.
    assert hashlib.sha256(ws.agent_notes().encode()).hexdigest() not in ws.LEGACY_AGENT_NOTES_SHA256
    assert len(ws.LEGACY_AGENT_NOTES_SHA256) == 5


def test_upgrade_adds_a_missing_agents_md_and_only_a_whole_run_touches_it(firm):
    (firm / "AGENTS.md").unlink()
    updates.accept_templates(firm, [".claude/rules"])
    assert not (firm / "AGENTS.md").exists(), "--accept must not write AGENTS.md"
    report = updates.update_templates(firm)
    assert report["floor"]["status"] == "added" and (firm / "AGENTS.md").read_text(encoding="utf-8") == ws.agent_notes()


def test_doctor_reports_the_block_and_the_hosts_that_load_it(firm):
    code, out, _ = invoke("doctor", "--workspace", str(firm), "--json")
    floor = json.loads(out)["instructions"]
    assert floor["state"] == "present" and floor["current"] and floor["version"] == ws.floor_block()[1]
    assert all(row["loads"] for row in floor["hosts"].values()) and not floor["next_actions"]
    (firm / "CLAUDE.md").write_text("# Firm notes only\n", encoding="utf-8")
    (firm / "AGENTS.md").write_text("# Firm\n", encoding="utf-8")
    code, out, _ = invoke("doctor", "--workspace", str(firm))
    assert "Torque block in AGENTS.md: MISSING (AGENTS.md has none)" in out
    assert "claude-code: NO" in out
    nexts = [line for line in out.splitlines() if line.startswith("Next:")]
    assert any("Add it with torque workspace upgrade" in line for line in nexts), nexts
    assert any("Add the line @AGENTS.md at the top of CLAUDE.md" in line for line in nexts), nexts


def test_doctor_names_an_agents_md_too_large_for_codex(firm):
    with (firm / "AGENTS.md").open("a", encoding="utf-8") as stream:
        stream.write("\n" + "x" * ws.CODEX_INSTRUCTION_BYTES + "\n")
    report = ws.floor_report(firm)
    assert report["current"] and not report["hosts"]["codex"]["loads"]
    assert any("Codex adds instruction files only up to" in line for line in report["next_actions"])
