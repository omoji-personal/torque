"""Antigravity (`agy`) copies of the packaged rules, recipes and worker roles.

Antigravity reads AGENTS.md and `.agents/` in the working folder, never `.claude/`.
Each copy is derived from the packaged Claude Code file when a workspace is
created or upgraded, so the two surfaces come from one source:

  rules/NAME.md     -> .agents/rules/NAME.md         `trigger: always_on` (`trigger: glob` with
                                                     `globs:` for a rule scoped by `paths:`)
                                                     and a description
  commands/NAME.md  -> .agents/skills/NAME/SKILL.md  `name` and `description`; also the /NAME command
  agents/NAME.md    -> .agents/agents/NAME.md        Antigravity tool names, worker only

Formats as observed with Antigravity CLI 1.3.1: a rule without a valid trigger
is discarded; a skill used as a command gets the typed text as the user message
and `$ARGUMENTS` is not expanded; Antigravity's own /help, /context and /undo
take those names; a worker that lists an unknown tool name can hang. The hook
file (.agents/hooks.json) is not derived: the owner registers the gate there
(gate_antigravity), as with the Claude Code hook.

The rule a build-only or connected workspace adds (workspace.mode_rules) is
copied with `rule` too, into the same `.agents/rules/`.
"""
from __future__ import annotations

import json
import re

from . import hosts

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
# Commands Antigravity answers itself; the recipe becomes /torque-NAME.
BUILTIN_COMMANDS = hosts.ANTIGRAVITY.builtin_commands
# One of those names written as a slash command (`/context`), not as part of a
# path or an address (project/context, /context/notes.md, https://help.example.com).
_BUILTIN_MENTION = hosts.mention_pattern(BUILTIN_COMMANDS)
INPUT_NOTE = "the text sent with this command, or the current request when none was sent."
# Claude Code tool name -> Antigravity tool name. Only names seen working are listed.
# Antigravity 1.4.9 still lists these names when a session starts (2026-10-28), though
# its calls and its hook input name the file tools read_file, write_file and edit_file
# (gate_antigravity reads both). A worker that lists the new names has not been tried,
# and an unknown name can hang one, so these stay.
TOOLS = {"Read": "view_file", "LS": "list_dir", "Grep": "grep_search", "Glob": "find_by_name",
         "Bash": "run_command", "Write": "write_to_file", "Edit": "replace_file_content"}
READ_TOOLS = ("view_file", "list_dir", "grep_search", "find_by_name")


def _split(text: str) -> tuple[dict[str, str], str]:
    """(top-level frontmatter fields, body); a file without frontmatter has no fields.
    A block list (`tools:` then `  - Read` lines) is joined with commas."""
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text
    fields: dict[str, str] = {}
    key = None
    for line in match.group(1).splitlines():
        item = re.match(r"\s+-\s+(.+)", line)
        if item and key is not None:
            fields[key] = ", ".join(part for part in (fields[key], item.group(1).strip()) if part)
            continue
        name, separator, value = line.partition(":")
        if separator and name.strip() and not line[:1].isspace():
            key = name.strip()
            fields[key] = value.strip()
    return fields, text[match.end():]


def _scalar(text: str) -> str:
    """A one-line YAML value: plain when that is safe, otherwise double-quoted."""
    text = " ".join(text.split())
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ,.()/'_-]*", text) and not text.endswith(" "):
        return text
    return json.dumps(text, ensure_ascii=False)


def _page(header: list[str], body: str) -> str:
    return "---\n" + "\n".join(header) + "\n---\n\n" + body.lstrip("\n")


def skill_name(name: str) -> str:
    return hosts.recipe_name(hosts.ANTIGRAVITY, name)


def _commands(body: str) -> str:
    """The text with each mention of a recipe Antigravity renames (`/context`)
    pointing at its name there (`/torque-context`); the bare name is
    Antigravity's own command."""
    return hosts.rename_mentions(hosts.ANTIGRAVITY, body)


def _paths(text: str) -> list[str]:
    """The globs of a Claude Code rule's `paths:` field, as an inline value or list
    (`paths: "a"`, `paths: ["a", "b"]`) or a block list of `  - a` lines. Read here, not
    from _split, because a glob can hold a comma (`*.{cls,trigger}`)."""
    match = _FRONTMATTER.match(text)
    if not match:
        return []
    lines = match.group(1).splitlines()
    for index, line in enumerate(lines):
        name, separator, value = line.partition(":")
        if not separator or name.strip() != "paths" or line[:1].isspace():
            continue
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            items = re.findall(r"\"([^\"]*)\"|'([^']*)'|([^,\s][^,]*)", value[1:-1])
            found = [next(part for part in item if part) for item in items if any(item)]
        elif value:
            found = [value]
        else:
            found = []
            for item in lines[index + 1:]:
                listed = re.match(r"\s+-\s+(.+)", item)
                if not listed:
                    break
                found.append(listed.group(1))
        return [path.strip().strip("\"'").strip() for path in found if path.strip().strip("\"'").strip()]
    return []


def _expand(glob: str) -> list[str]:
    """One glob as globs without braces: `*.{cls,trigger}` -> `*.cls`, `*.trigger`.
    Antigravity's `globs:` is one comma-separated value, so a comma inside braces
    would split the pattern in two."""
    match = re.search(r"\{([^{}]*)\}", glob)
    if not match:
        return [glob]
    return [expanded for choice in match.group(1).split(",")
            for expanded in _expand(glob[:match.start()] + choice + glob[match.end():])]


# Glob syntax Claude Code reads in `paths:` that a `glob` trigger cannot say: an
# exclusion. Such a rule stays always on, with a note in its copy, rather than vanishing.
UNSUPPORTED_GLOB = re.compile(r"^!")


def rule(name: str, text: str) -> str | None:
    """A rule. One scoped with `paths:` becomes a `trigger: glob` rule with those
    globs (braces expanded), so it loads when a matching file is in play, as in
    Claude Code; every other rule is `trigger: always_on`. A `paths:` field with no glob,
    or with a pattern a glob trigger cannot express, keeps the rule always on, the second
    with a note at the top of the copy saying why: no rule is dropped."""
    fields, body = _split(text)
    globs = _paths(text) if "paths" in fields else []
    body = _commands(body)
    title = re.search(r"^#\s+(.+?)\s*$", body, re.M)
    description = fields.get("description") or _scalar(title.group(1) if title else name)
    unsupported = [glob for glob in globs if UNSUPPORTED_GLOB.search(glob)]
    if unsupported:
        note = ("<!-- torque: paths " + ", ".join(unsupported) + " cannot be written as an Antigravity glob "
                "(an exclusion), so this copy is always on. -->\n\n")
        return _page(["trigger: always_on", "description: " + description], note + body.lstrip("\n"))
    if globs:
        expanded = list(dict.fromkeys(part for glob in globs for part in _expand(glob)))
        return _page(["trigger: glob", "globs: " + json.dumps(", ".join(expanded), ensure_ascii=False),
                      "description: " + description], body)
    return _page(["trigger: always_on", "description: " + description], body)


def skill(name: str, text: str) -> str | None:
    """A recipe as a skill and slash command; None without a description
    (Antigravity cannot offer a skill that has none)."""
    fields, body = _split(text)
    if not fields.get("description"):
        return None
    # The recipe's own heading (`# /help`) is renamed with every other mention.
    body = _commands(body).replace("$ARGUMENTS", INPUT_NOTE)
    return _page(["name: " + skill_name(name), "description: " + fields["description"]], body)


def _tools(declared: str | None) -> list[str]:
    """Antigravity tool names for a role. A role that names no tools has them
    all in Claude Code, so it gets every listed tool; unknown names are dropped."""
    if not declared:
        return list(TOOLS.values())
    names = [part.strip().strip("\"'") for part in declared.strip("[]").split(",")]
    found = [TOOLS[name] for name in names if name in TOOLS]
    return list(dict.fromkeys(found)) or list(READ_TOOLS)


def agent(name: str, text: str) -> str | None:
    """A worker role; None without a description."""
    fields, body = _split(text)
    if not fields.get("description"):
        return None
    header = ["name: " + (fields.get("name") or name), "description: " + fields["description"], "tools:"]
    header += ["  - " + tool for tool in _tools(fields.get("tools"))] + ["mainAgent: false", "subagent: true"]
    return _page(header, _commands(body))


_GROUPS = (("rules", rule, ".agents/rules/{}.md"),
           ("commands", skill, ".agents/skills/{}/SKILL.md"),
           ("agents", agent, ".agents/agents/{}.md"))


def surface(data) -> dict[str, tuple[str, str]]:
    """{workspace path: (text, packaged source)} for every derived file. `data`
    is the packaged data folder (torque/data)."""
    out: dict[str, tuple[str, str]] = {}
    for group, make, target in _GROUPS:
        folder = data.joinpath(group)
        if not folder.is_dir():
            continue
        for item in sorted(folder.iterdir(), key=lambda entry: entry.name):
            if not item.is_file() or not item.name.endswith(".md"):
                continue
            stem = item.name[:-3]
            text = make(stem, item.read_text(encoding="utf-8"))
            if text is not None:
                shown = skill_name(stem) if group == "commands" else stem
                out[target.format(shown)] = (text, f"torque/data/{group}/{item.name} (Antigravity copy)")
    return out
