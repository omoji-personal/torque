"""Antigravity (`agy`) copies of the packaged rules, recipes and worker roles.

Antigravity reads AGENTS.md and `.agents/` in the working folder, never `.claude/`.
Each copy is derived from the packaged Claude Code file when a workspace is
created or upgraded, so the two surfaces come from one source:

  rules/NAME.md     -> .agents/rules/NAME.md         `trigger: always_on` and a description
  commands/NAME.md  -> .agents/skills/NAME/SKILL.md  `name` and `description`; also the /NAME command
  agents/NAME.md    -> .agents/agents/NAME.md        Antigravity tool names, worker only

Formats as observed with Antigravity CLI 1.3.1: a rule without a valid trigger
is discarded; a skill used as a command gets the typed text as the user message
and `$ARGUMENTS` is not expanded; Antigravity's own /help, /context and /undo
take those names; a worker that lists an unknown tool name can hang. The hook
file (.agents/hooks.json) is not derived: the owner registers the gate there
(gate_antigravity), as with the Claude Code hook.
"""
from __future__ import annotations

import json
import re

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
# Commands Antigravity answers itself; the recipe becomes /torque-NAME.
BUILTIN_COMMANDS = ("help", "context", "undo")
INPUT_NOTE = "the text sent with this command, or the current request when none was sent."
# Claude Code tool name -> Antigravity tool name. Only names seen working are listed.
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


def rule(name: str, text: str) -> str | None:
    """An always-on rule. A rule scoped with `paths:` is not copied: it would
    otherwise apply everywhere."""
    fields, body = _split(text)
    if "paths" in fields:
        return None
    title = re.search(r"^#\s+(.+?)\s*$", body, re.M)
    description = fields.get("description") or _scalar(title.group(1) if title else name)
    return _page(["trigger: always_on", "description: " + description], body)


def skill_name(name: str) -> str:
    return "torque-" + name if name in BUILTIN_COMMANDS else name


def skill(name: str, text: str) -> str | None:
    """A recipe as a skill and slash command; None without a description
    (Antigravity cannot offer a skill that has none)."""
    fields, body = _split(text)
    if not fields.get("description"):
        return None
    shown = skill_name(name)
    body = body.replace(f"# /{name}\n", f"# /{shown}\n", 1).replace("$ARGUMENTS", INPUT_NOTE)
    return _page(["name: " + shown, "description: " + fields["description"]], body)


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
    return _page(header, body)


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
