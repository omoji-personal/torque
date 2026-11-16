"""`torque workflows list` is short: every recipe by name with its use-when line, then the skills.

Each host's agent reads this listing before it picks a recipe, so it costs context on every
task. The full description and kind stay in `list --json` and in the recipe itself."""
import contextlib
import io
import json
from pathlib import Path

from torque import cli

REPO = Path(__file__).resolve().parents[1]
CATALOGUE = json.loads((REPO / "workflows" / "catalogue.json").read_text(encoding="utf-8"))
# The listing was 11,742 characters with every full description (2.0.0a23); keep it near half.
BUDGET = 6_500


def listing(*words) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert cli.main(["workflows", *words]) == 0
    return out.getvalue()


def test_the_list_names_every_recipe_once_with_its_use_when_line():
    text = listing("list")
    recipes = text.split("\nPractice skills", 1)[0]
    lines = recipes.splitlines()[1:]
    assert len(lines) == len(CATALOGUE)
    for entry, line in zip(CATALOGUE, lines):
        assert line.split()[0] == entry["name"] and line.endswith(" " + entry["use_when"]), line
        assert entry["description"] not in line
    assert "[guided]" not in text and "[native]" not in text


def test_the_list_stays_within_its_budget():
    text = listing()
    assert len(text) <= BUDGET, len(text)
    assert all(len(line) <= 160 for line in text.splitlines())


def test_the_json_catalogue_keeps_every_field_and_adds_the_use_when_line():
    rows = json.loads(listing("list", "--json"))
    assert [row["name"] for row in rows] == [entry["name"] for entry in CATALOGUE]
    for row in rows:
        assert {"name", "group", "description", "use_when", "kind", "implementation", "source_command"} <= set(row)


def test_workflows_help_says_what_each_listing_holds(monkeypatch):
    monkeypatch.setenv("COLUMNS", "400")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            cli.main(["workflows", "--help"])
        except SystemExit:
            pass
    text = " ".join(out.getvalue().split())
    for phrase in ("each recipe's name and when to use it", "name, group, description, use_when, kind",
                   "guided: a recipe that uses existing tools", "compatibility: an old name kept",
                   "show NAME/REFERENCE"):
        assert phrase in text, phrase


def test_the_drift_check_refuses_a_missing_or_long_use_when_line(tmp_path):
    import importlib.util
    import shutil
    for relative in ("workflows", ".claude/rules", ".claude/agents", ".agents/skills", "lessons", "floor",
                     ".claude/commands", "src/torque/data"):
        shutil.copytree(REPO / relative, tmp_path / relative, ignore=shutil.ignore_patterns("__pycache__"))
    spec = importlib.util.spec_from_file_location("sync_workflows_list", REPO / "scripts" / "sync-workflows.py")
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)
    rows = [dict(row) for row in CATALOGUE]
    rows[0]["use_when"] = "x" * 81
    del rows[1]["use_when"]
    (tmp_path / "workflows" / "catalogue.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    problems, _, _ = sync.drift(tmp_path)
    assert any(f"{rows[0]['name']}'s use_when is not one line" in problem for problem in problems), problems
    assert any(f"{rows[1]['name']} has no use_when" in problem for problem in problems), problems
