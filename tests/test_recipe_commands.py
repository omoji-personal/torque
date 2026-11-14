"""Every `torque ...` command a recipe or a standing rule shows is one Torque's own parser takes.

A consultant's agent copies these lines at the moment of the work: a subcommand that does not
exist, or an option that subcommand does not take, costs a failed call and a guess. The test
reads each command and hands it to the real parser (Torque's, or the bundled package's for a
delegated route) without running anything.

How a line is read:
- Lines in a fenced or indented code block are complete commands: they must parse as written.
- A command in backticks inside a sentence often names only the part that matters
  (`torque check result`, `torque lesson helpful <id>`): its subcommands must exist and each
  option must belong to that subcommand.
- `<anything>` is a value. `[...]` marks an optional part, which is checked as if present.
  `a|b` is a choice of values; the first is checked. A bare `...` stands for options shown
  elsewhere, so that line is checked as a partial one. A pipe or redirection ends the command.

Other groups add files to SOURCES; lines that are deliberately not commands go in ALLOWED with
their reason. The same files are checked for the skills, recipes and repository files they cite.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import inspect
import io
import re
import shlex
import sys
from pathlib import Path

import pytest

from torque import cli

REPO = Path(__file__).resolve().parents[1]
SOURCES = ("workflows/*.md", ".claude/rules/*.md")

# (file, start of the command as written): why it is not checked as a command.
ALLOWED: dict[tuple[str, str], str] = {}

# Values for placeholders that fill an option with fixed choices or a number.
PLACEHOLDER_VALUES = {"status": "executed", "count": "3", "n": "3", "limit": "3"}
ELISION = "..."
SHELL_ENDS = ("|", ">", ">>", "2>", "2>&1", "&&", ";")


def source_files() -> list[Path]:
    found: list[Path] = []
    for pattern in SOURCES:
        found.extend(sorted(REPO.glob(pattern)))
    return found


def _placeholder(match: re.Match) -> str:
    name = match.group(0)[1:-1].strip().lower()
    return PLACEHOLDER_VALUES.get(name, "x1")


def commands_in(text: str) -> list[tuple[int, str, bool]]:
    """(line number, command, complete) for each `torque ...` command in a Markdown text."""
    lines = text.splitlines()
    found: list[tuple[int, str, bool]] = []
    prose = []
    fenced = False
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("```"):
            fenced = not fenced
            prose.append("")
            index += 1
            continue
        indented = line.startswith("    ") and not stripped.startswith(("-", "*", "`"))
        if (fenced or indented) and stripped.startswith("torque "):
            start = index
            command = stripped
            while command.endswith("\\") and index + 1 < len(lines):
                index += 1
                command = command[:-1] + " " + lines[index].strip()
            found.append((start + 1, command, True))
            prose.extend([""] * (index - start + 1))
            index += 1
            continue
        prose.append("" if fenced else line)
        index += 1
    body = "\n".join(prose)
    for match in re.finditer(r"`(torque\s[^`]*)`", body):
        found.append((body.count("\n", 0, match.start()) + 1, " ".join(match.group(1).split()), False))
    return sorted(found)


def words_of(command: str) -> tuple[list[str], bool]:
    """The words after `torque`, with placeholders filled; and whether `...` elides options."""
    filled = re.sub(r"<[^<>`]+>", _placeholder, command)
    words = shlex.split(filled, comments=True)
    out: list[str] = []
    elided = False
    for word in words[1:]:
        if word in SHELL_ENDS or word.startswith((">", "|")):
            break
        word = word.strip("[]")
        if word == ELISION:
            elided = True
            continue
        if not word:
            continue
        if "|" in word and not word.startswith("-"):
            word = word.split("|", 1)[0]
        out.append(word)
    return out, elided


class _Captured(BaseException):
    def __init__(self, parser: argparse.ArgumentParser, argv: list[str]):
        super().__init__()
        self.parser, self.argv = parser, argv


def parser_and_words(words: list[str]) -> tuple[argparse.ArgumentParser, list[str]]:
    """The parser Torque would hand these words to, and the words it would hand it, found by
    running Torque's own entry point up to its first parse and no further. The entry point may
    call parse_args or parse_known_args (the top level calls the second and refuses leftovers
    itself); the first call of either is the outermost one, so it is the one captured."""
    original_parse, original_known = argparse.ArgumentParser.parse_args, argparse.ArgumentParser.parse_known_args
    original_dispatch = cli._dispatch

    def capture(self, args=None, namespace=None):
        raise _Captured(self, list(sys.argv[1:] if args is None else args))

    def dispatch(route, argv, display=None):
        names = ("--workspace", "--client", "--initiative") if route == "lesson" else ("--workspace", "--client")
        args, _ = cli._scope_options(argv, names)
        module = importlib.import_module(cli.DELEGATES[route])
        previous = sys.argv
        sys.argv = [display or f"torque {route}", *args]
        try:
            if "argv" in inspect.signature(module.main).parameters:
                return module.main(argv=args)
            return module.main()
        finally:
            sys.argv = previous

    argparse.ArgumentParser.parse_args = capture
    argparse.ArgumentParser.parse_known_args = capture
    cli._dispatch = dispatch
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cli.main(list(words))
    except _Captured as caught:
        return caught.parser, caught.argv
    finally:
        argparse.ArgumentParser.parse_args = original_parse
        argparse.ArgumentParser.parse_known_args = original_known
        cli._dispatch = original_dispatch
    raise AssertionError(f"torque {' '.join(words)} reached no argument parser")


def _subparsers(parser: argparse.ArgumentParser):
    return next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)


def names_problem(parser: argparse.ArgumentParser, words: list[str], elided: bool = False) -> str | None:
    """Each subcommand exists and each option belongs to the subcommand it follows. Where `...`
    stands for the subcommand itself, an option is accepted when one of the subcommands takes it."""
    index, chosen = 0, False
    while index < len(words):
        word = words[index]
        if word.startswith("-") and word != "-":
            name = word.split("=", 1)[0]
            action = parser._option_string_actions.get(name)
            sub = _subparsers(parser)
            if action is None and elided and sub is not None and not chosen:
                action = next((child._option_string_actions[name] for child in sub.choices.values()
                               if name in child._option_string_actions), None)
            if action is None:
                return f"`{parser.prog}` takes no option {name}"
            if action.nargs != 0 and "=" not in word and index + 1 < len(words) \
                    and not words[index + 1].startswith("-"):
                index += 1
        else:
            sub = _subparsers(parser)
            if sub is not None and not chosen:
                if word not in sub.choices:
                    return f"`{parser.prog}` has no subcommand {word!r}"
                parser = sub.choices[word]
            else:
                chosen = True
        index += 1
    return None


def parse_problem(parser: argparse.ArgumentParser, words: list[str]) -> str | None:
    """None when the parser takes the words; otherwise what it said. Torque's own parser raises
    its usage error instead of exiting; a package's parser exits with code 2."""
    said = io.StringIO()
    usage_error = getattr(cli, "UsageError", ())
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(said):
            parser.parse_args(words)
    except SystemExit as exc:
        if exc.code not in (0, None):
            return said.getvalue().strip().splitlines()[-1] if said.getvalue().strip() else f"exit {exc.code}"
    except usage_error as exc:
        return f"{exc.parser.prog}: error: {exc.message}"
    return None


def problem_of(command: str, complete: bool) -> str | None:
    try:
        words, elided = words_of(command)
    except ValueError as exc:
        return f"cannot be split into words ({exc})"
    if not words:
        return "names no subcommand"
    parser, argv = parser_and_words(words)
    if complete and not elided:
        return parse_problem(parser, argv)
    return names_problem(parser, argv, elided)


def all_commands():
    for path in source_files():
        relative = path.relative_to(REPO).as_posix()
        for line, command, complete in commands_in(path.read_text(encoding="utf-8")):
            if any(relative == f and command.startswith(start) for f, start in ALLOWED):
                continue
            yield relative, line, command, complete


def test_every_command_a_recipe_or_rule_shows_is_one_the_parser_takes():
    problems = []
    seen = 0
    for relative, line, command, complete in all_commands():
        seen += 1
        problem = problem_of(command, complete)
        if problem:
            problems.append(f"{relative}:{line}: {command}\n    {problem}")
    assert seen > 50, seen
    assert not problems, "\n".join(problems)


def test_every_allowed_line_still_exists_and_says_why():
    texts = {path.relative_to(REPO).as_posix(): path.read_text(encoding="utf-8") for path in source_files()}
    for (relative, start), reason in ALLOWED.items():
        assert reason.strip(), (relative, start)
        assert relative in texts and start in " ".join(texts[relative].split()), (relative, start)


SKILL = re.compile(r"\bskills? `([a-z0-9-]+)`")
SKILL_FILE = re.compile(r"`((?:salesforce|torque)-[a-z0-9-]+)/(references/[A-Za-z0-9_.-]+\.md)`")
RECIPE = re.compile(r"`/([a-z][a-z0-9-]*)(?:[ `])")
REPO_FILE = re.compile(r"`((?:docs|src|packages|scripts|workflows|tests|lessons|examples|\.claude|\.agents)"
                       r"/[A-Za-z0-9_./-]+)`")


def citations(text: str):
    text = " ".join(text.split())
    for name in SKILL.findall(text):
        yield "skill", name, REPO / ".agents" / "skills" / name / "SKILL.md"
    for name, reference in SKILL_FILE.findall(text):
        yield "skill file", f"{name}/{reference}", REPO / ".agents" / "skills" / name / reference
    for name in RECIPE.findall(text):
        yield "recipe", f"/{name}", REPO / "workflows" / f"{name}.md"
    for path in REPO_FILE.findall(text):
        if "*" not in path and "<" not in path:
            yield "file", path, REPO / path.rstrip("/")


def test_every_skill_recipe_and_file_a_recipe_or_rule_cites_exists():
    missing = []
    for path in source_files():
        for kind, name, target in citations(path.read_text(encoding="utf-8")):
            if not target.exists():
                missing.append(f"{path.relative_to(REPO).as_posix()}: {kind} {name} ({target.relative_to(REPO)})")
    assert not missing, "\n".join(missing)


@pytest.mark.parametrize("command,complete,reason", [
    ("torque lesson stale <id> --workspace <private-path> --client <client-name>", True, "--reason"),
    ("torque lesson capture <lesson-text> --workspace <private-path> --no-such-option x", True, "--no-such-option"),
    ("torque check metadatas force-app", False, "metadatas"),
    ("torque session add ... --learnd x", True, "--learnd"),
    ("torque recover run <snapshot-id> --org <org-alias> --workspace <private-path> --client <client-name> "
     "--no-such-option", True, "--no-such-option"),
    ("torque recover run <snapshot-id> --workspace <private-path> --client <client-name>", True, "--org"),
    # Torque's own top level: its parser raises a usage error instead of exiting.
    ("torque context --workspace <private-path> --client <client-name> --no-such-option", True, "--no-such-option"),
    ("torque session add --workspace <private-path> --client <client-name> --summary x --status done", True,
     "invalid choice"),
])
def test_the_checker_finds_a_wrong_command(command, complete, reason):
    problem = problem_of(command, complete)
    assert problem and reason in problem, (command, problem)


@pytest.mark.parametrize("command,complete", [
    ("torque lesson stale <id> --reason <why> --workspace <private-path> --client <client-name>", True),
    ("torque session add --workspace <private-path> --client <client-name> --summary <summary-text> "
     "--status <status> [--evidence <artifact-path>]", True),
    ("torque check result", False),
    ("torque lesson for --error \"<first line of the error>\"", False),
    ("torque lesson list [--state candidate|active|retired|stale]", True),
    ("torque recover run <snapshot-id> --org <org-alias> --workspace <private-path> --client <client-name>", True),
])
def test_the_checker_accepts_a_right_command(command, complete):
    assert problem_of(command, complete) is None, problem_of(command, complete)
