"""The agent hosts Torque runs under: one registry.

A host is the program that runs the AI session: Claude Code (`claude`) or
Antigravity (`agy`). An entry holds what Torque has to know about one of them:
the binary `torque launch` starts, the marks that show a process belongs to its
session, the folder it reads its playbook from, the file its hook is registered
in, the options a delegated launch may pass to it, the tool names a gate
message suggests, and the slash commands it answers itself.

Launch, the launch record, the presence check, doctor's choice of host and the
mode rules read these values from here. The modules that write a host's files
or speak its hook format (workspace.py, template_updates.py, antigravity.py,
gate.py, gate_antigravity.py and the hook reports in cli.py) still spell out
their own `.claude` and `.agents` paths. docs/hosts.md says what works under
each host and what was verified."""
from __future__ import annotations

from dataclasses import dataclass
import re

# Set by a hook in its own process to state the host it runs under (the
# Antigravity adapter does); unset means Claude Code. launch.verify_launch reads it.
HOOK_HOST_ENV = "TORQUE_HOOK_HOST"


class UnknownHost(ValueError):
    """A host name that is not in the registry."""


@dataclass(frozen=True)
class Host:
    key: str
    name: str
    # What `torque launch` starts.
    binary: str
    # Environment variables the host sets for every tool subprocess.
    env_markers: tuple[str, ...]
    # A process whose command name contains one of these is this host.
    process_markers: tuple[str, ...]
    # Where the host reads rules, recipes and worker roles, and where its hook is registered.
    playbook: str
    hook_file: str
    # R49 (amended): the only options a delegated launch passes through, an
    # allowlist (fail closed). Options that take a value take the next word or
    # `=value`; a pinned option only with the one value given for it.
    allowed_flags: tuple[str, ...]
    allowed_value_options: tuple[str, ...]
    pinned_options: tuple[tuple[str, str], ...]
    # Removed from a delegated launch's child environment (and refused when the caller sets it).
    refused_env: tuple[str, ...]
    # The tools a gate message suggests in place of one it does not recognise.
    tool_hint: str
    aliases: tuple[str, ...] = ()
    # Slash commands the host answers itself: a recipe of the same name is installed
    # there as torque-NAME (recipe_name), so both keep working.
    builtin_commands: tuple[str, ...] = ()


CLAUDE = Host(
    key="claude", name="Claude Code", binary="claude",
    # Claude Code sets these for every tool subprocess (verified, docs/connected-approval.md).
    env_markers=("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"),
    process_markers=("claude",),
    playbook=".claude", hook_file=".claude/settings.json",
    allowed_flags=("-p", "--print", "--include-partial-messages", "--replay-user-messages", "--verbose",
                   "--no-session-persistence"),
    allowed_value_options=("--input-format", "--output-format", "--model", "--fallback-model", "--effort",
                           "--append-system-prompt", "--max-budget-usd", "--json-schema", "--session-id", "--name"),
    pinned_options=(("--permission-mode", "default"),),
    refused_env=("CLAUDE_CODE_SIMPLE",),
    tool_hint="Use Bash, Read, Edit, Write, Grep or Glob",
    # Claude Code's command reference (code.claude.com/docs/en/commands, 2026-10-28). A
    # project skill replaces a built-in of its name in a terminal session, but not an
    # alias: /undo stays /rewind's alias, so typing it most likely runs a checkpoint
    # rewind, not the recipe (not tried in a live session).
    builtin_commands=("help", "context", "status", "security-review", "undo"))

ANTIGRAVITY = Host(
    key="antigravity", name="Antigravity", binary="agy",
    # NOT verified yet: no variable is known that Antigravity sets for every tool
    # subprocess, so none is listed and this check finds no Antigravity session.
    # Add one here only after it has been seen in a live session.
    env_markers=(),
    # The names of the CLI and the application. Not verified in a live session on
    # macOS or Linux that the session process shows under one of them.
    process_markers=("agy", "antigravity"),
    playbook=".agents", hook_file=".agents/hooks.json",
    # Kept to what an unattended run needs. `-p`/`--print` is the headless switch;
    # the prompt after it passes as text.
    allowed_flags=("-p", "--print"),
    allowed_value_options=("--model", "--add-dir", "--output-format", "--print-timeout"),
    pinned_options=(),
    # None known.
    refused_env=(),
    # The file tools by Antigravity 1.4.9's names; 1.3.1 called them view_file,
    # replace_file_content and write_to_file.
    tool_hint="Use run_command, read_file, edit_file, write_file, grep_search or find_by_name",
    aliases=("agy", "gemini"),
    # Seen taking these names with Antigravity CLI 1.3.1.
    builtin_commands=("help", "context", "undo"))

HOSTS = (CLAUDE, ANTIGRAVITY)
KEYS = tuple(host.key for host in HOSTS)
DEFAULT = CLAUDE


def get(key) -> Host | None:
    """The host with exactly this key, or None. For values a record or a hook
    wrote, where an alias or another spelling is not accepted."""
    return next((host for host in HOSTS if host.key == key), None)


def resolve(name) -> Host:
    """The host a person named: a key, or the aliases `agy` and `gemini` for
    Antigravity. Case and surrounding spaces do not matter."""
    wanted = name.strip().casefold() if isinstance(name, str) else None
    for host in HOSTS:
        if wanted and (wanted == host.key or wanted in host.aliases):
            return host
    raise UnknownHost(f"unknown host {name!r}; choose {' or '.join(KEYS)}")


def for_workspace(config: dict) -> Host:
    """The host a workspace names with the optional "host" key of workspace.json;
    Claude Code when it names none. A value that is not a host refuses."""
    if not isinstance(config, dict) or "host" not in config:
        return DEFAULT
    try:
        return resolve(config["host"])
    except UnknownHost as exc:
        raise UnknownHost(f'workspace.json "host": {exc}') from None


def recipe_name(host: Host, name: str) -> str:
    """The name a packaged recipe has under `host`: torque-NAME when the host
    answers a command of that name itself, otherwise its catalogue name."""
    return "torque-" + name if name in host.builtin_commands else name


def mention_pattern(names) -> re.Pattern:
    """One of `names` written as a slash command (`/context`), not as part of a
    path or an address (project/context, /context/notes.md, https://help.example.com)."""
    words = "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    return re.compile(r"(?<![\w/.:~})-])/(" + words + r")(?![\w/-]|\.\w)")


def rename_mentions(host: Host, text: str) -> str:
    """`text` with each mention of a recipe `host` renames pointing at its name
    there; the bare name stays the host's own command."""
    if not host.builtin_commands:
        return text
    return mention_pattern(host.builtin_commands).sub(lambda match: "/" + recipe_name(host, match.group(1)), text)


def env_markers() -> tuple[str, ...]:
    """Every host's environment markers."""
    return tuple(dict.fromkeys(marker for host in HOSTS for marker in host.env_markers))


def process_markers() -> tuple[str, ...]:
    """Every host's process-name markers."""
    return tuple(dict.fromkeys(marker for host in HOSTS for marker in host.process_markers))
