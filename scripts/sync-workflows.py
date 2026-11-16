#!/usr/bin/env python3
"""Generate every copy of the conversational surface from its one source; --check fails on drift.

Edit only the sources:
  workflows/NAME.md         one recipe each. A line `{{lessons --task T}}` or an inline `{{test-records}}`
                            pulls in the text of workflows/shared/NAME.md, written once for every recipe
  workflows/shared/*.md     text several recipes carry: `{args}` stands for the words after the name
  workflows/catalogue.json  each recipe's name, group, description and kind
  .claude/rules/  .claude/agents/  .agents/skills/  lessons/  floor/AGENTS-floor.md

Generated, never edited by hand:
  .claude/commands/NAME.md  the rendered recipe with its description and the conversation-input footer
  src/torque/data/          the wheel's byte copy of all of it (a workspace's .agents/ copies, which
                            Antigravity and Codex read, are derived from it at init and upgrade)

  python scripts/sync-workflows.py           write the generated files
  python scripts/sync-workflows.py --check   change nothing; exit 1 on any source problem and on any
                                             generated file that differs, is missing or should not be there
"""
import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
KINDS = {"guided": "Guided recipe using existing tools.",
         "native": "Native CLI with conversational interpretation.",
         "compatibility": "Compatibility recipe."}
INTERFACE = "**Interface:** {} A slash command is an assistant instruction, not a separate shell executable."
FOOTER = ("\n**Conversation input:** $ARGUMENTS\n"
          "Use supplied context and existing authorization. Ask only for consequential missing information. "
          "Keep work and evidence in the selected private client workspace.\n")
SHARED = re.compile(r"\{\{([a-z][a-z0-9-]*)((?: [^{}\n]+)?)\}\}")
BUNDLED = {"catalogue.json", "commands", "rules", "agents", "skills", "lessons", "floor"}
LOCAL_ONLY = {".claude/agents/torque-marketing.md"}   # git-ignored, never shipped


def catalogue(root: Path) -> list:
    return json.loads((root / "workflows/catalogue.json").read_text(encoding="utf-8"))


def shared_texts(root: Path) -> dict:
    return {path.stem: path.read_text(encoding="utf-8").rstrip("\n")
            for path in sorted((root / "workflows/shared").glob("*.md"))}


def render(name: str, text: str, shared: dict, problems: list, used: set) -> str:
    """The recipe with each {{NAME args}} replaced by its shared text."""
    def fill(match: re.Match) -> str:
        key, words = match.group(1), match.group(2).strip()
        if key not in shared:
            problems.append(f"workflows/{name}.md: no shared text workflows/shared/{key}.md")
            return match.group(0)
        used.add(key)
        template = shared[key]
        if ("{args}" in template) != bool(words):
            problems.append(f"workflows/{name}.md: {{{{{key}}}}} " + ("needs the words for {args}" if words == ""
                                                                       else "takes no words"))
        return template.replace("{args}", words)
    body = SHARED.sub(fill, text)
    if "{{" in body or "}}" in body:
        problems.append(f"workflows/{name}.md: a {{{{...}}}} that names no shared text")
    return body


def recipes(root: Path, problems: list) -> dict:
    """{".claude/commands/NAME.md": bytes} rendered from the sources; source problems go to `problems`."""
    records = catalogue(root)
    shared = shared_texts(root)
    used: set = set()
    result = {}
    names = [record.get("name") for record in records]
    for duplicate in sorted({name for name in names if names.count(name) > 1}):
        problems.append(f"workflows/catalogue.json: {duplicate} is listed more than once")
    for record in records:
        name = record.get("name")
        source = root / "workflows" / f"{name}.md"
        missing = [key for key in ("name", "group", "description", "kind") if not record.get(key)]
        if missing:
            problems.append(f"workflows/catalogue.json: {name} has no {', '.join(missing)}")
            continue
        if record["kind"] not in KINDS:
            problems.append(f"workflows/catalogue.json: {name} has the unknown kind {record['kind']}")
            continue
        if not source.is_file():
            problems.append(f"workflows/{name}.md is missing (the catalogue lists {name})")
            continue
        text = source.read_text(encoding="utf-8")
        # The name, the description and the interface line are what a host routes by: one
        # source each (the catalogue), and the recipe must say the same.
        head = f"# /{name}\n\n{record['description']}\n\n{INTERFACE.format(KINDS[record['kind']])}\n"
        if not text.startswith(head):
            problems.append(f"workflows/{name}.md does not start with its heading, the catalogue description and "
                            f"the interface line of kind {record['kind']}")
        body = render(name, text, shared, problems, used)
        page = "---\ndescription: " + json.dumps(record["description"]) + "\n---\n\n" + body + FOOTER
        result[f".claude/commands/{name}.md"] = page.encode("utf-8")
    listed = set(names)
    for path in sorted((root / "workflows").glob("*.md")):
        if path.stem not in listed:
            problems.append(f"workflows/{path.name} is not in workflows/catalogue.json")
    for key in sorted(set(shared) - used):
        problems.append(f"workflows/shared/{key}.md is used by no recipe")
    return result


def bundle(root: Path, commands: dict) -> dict:
    """{"src/torque/data/KEY": bytes}: the wheel's copy, commands taken from the rendering."""
    result = {"catalogue.json": (root / "workflows/catalogue.json").read_bytes()}
    for key, data in commands.items():
        result["commands/" + key.rsplit("/", 1)[1]] = data
    for directory, target in ((".claude/rules", "rules"), (".claude/agents", "agents")):
        for path in sorted((root / directory).glob("*.md")):
            if f"{directory}/{path.name}" not in LOCAL_ONLY:
                result[f"{target}/{path.name}"] = path.read_bytes()
    skills = root / ".agents/skills"
    for path in sorted([*skills.glob("*/SKILL.md"), *skills.glob("*/references/*.md")]):
        result["skills/" + path.relative_to(skills).as_posix()] = path.read_bytes()
    # Platform lessons: one Markdown file each, shipped read-only (docs/lessons.md).
    for path in sorted((root / "lessons").glob("*.md")):
        result[f"lessons/{path.name}"] = path.read_bytes()
    # The floor: the always-loaded block `workspace init` and `upgrade` write into AGENTS.md.
    result["floor/AGENTS-floor.md"] = (root / "floor/AGENTS-floor.md").read_bytes()
    return {"src/torque/data/" + key: data for key, data in result.items()}


def generated(root: Path) -> dict:
    """{repository path: bytes} of every generated file that exists now."""
    found = {path.relative_to(root).as_posix(): path.read_bytes()
             for path in (root / ".claude/commands").glob("*")
             if path.is_file()}
    data = root / "src/torque/data"
    for path in data.rglob("*"):
        key = path.relative_to(data).as_posix()
        if path.is_file() and "__pycache__" not in path.parts and (key == "catalogue.json"
                                                                     or key.split("/")[0] in BUNDLED):
            found["src/torque/data/" + key] = path.read_bytes()
    return found


def drift(root: Path) -> tuple[list, list, dict]:
    """(source problems, generated paths that differ, are missing or are extra, the expected files)."""
    problems: list = []
    commands = recipes(root, problems)
    expected = {**commands, **bundle(root, commands)}
    actual = generated(root)
    differ = sorted(key for key in set(expected) | set(actual) if expected.get(key) != actual.get(key))
    return problems, differ, expected


def describe(key: str, expected: dict, actual_exists: bool) -> str:
    if key not in expected:
        return f"{key} (should not be there)"
    return f"{key} ({'differs' if actual_exists else 'missing'})"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="change nothing; exit 1 on drift")
    parser.add_argument("--root", type=Path, default=ROOT, help="the checkout (default: this one)")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    problems, differ, expected = drift(root)
    if problems:
        print("Recipe sources need a fix (nothing was written):\n  " + "\n  ".join(problems))
        return 1
    if args.check:
        if differ:
            print("Generated files differ from their sources; run python scripts/sync-workflows.py:\n  "
                  + "\n  ".join(describe(key, expected, (root / key).is_file()) for key in differ))
            return 1
        print(f"Generated recipes and bundled resources match their sources ({len(expected)} files).")
        return 0
    for key in differ:
        path = root / key
        if key not in expected:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(expected[key])
    print(f"Generated {len(expected)} files from their sources ({len(differ)} written or removed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
