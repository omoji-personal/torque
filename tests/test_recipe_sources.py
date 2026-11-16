"""One hand-edited source per recipe; every other copy is generated, and one check fails on drift.

Sources: workflows/NAME.md and workflows/catalogue.json, with the rules, roles, skills, lessons
and floor. `scripts/sync-workflows.py` renders `.claude/commands/` and the wheel's
`src/torque/data/` from them; `--check` changes nothing and fails on a hand-edited copy, a
missing one, a stray one and a source that disagrees with the catalogue."""
import importlib.util
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SOURCES = ("workflows", ".claude/rules", ".claude/agents", ".agents/skills", "lessons", "floor")
GENERATED = (".claude/commands", "src/torque/data")


def _generator():
    spec = importlib.util.spec_from_file_location("sync_workflows", REPO / "scripts" / "sync-workflows.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYNC = _generator()


def run_check(root: Path, capsys) -> tuple[int, str]:
    code = SYNC.main(["--check", "--root", str(root)])
    return code, capsys.readouterr().out


@pytest.fixture
def tree(tmp_path):
    for relative in (*SOURCES, *GENERATED):
        shutil.copytree(REPO / relative, tmp_path / relative, ignore=shutil.ignore_patterns("__pycache__"))
    return tmp_path


def test_the_checkout_has_one_source_per_recipe_and_no_drift(capsys):
    code, out = run_check(REPO, capsys)
    assert code == 0, out
    # The generator the single check replaced is gone, so no second path writes the copies.
    assert not (REPO / "workflows" / "sync_adapters.py").exists()
    workflow = (REPO / ".github" / "workflows" / "validate.yml").read_text(encoding="utf-8")
    assert "python scripts/sync-workflows.py --check" in workflow and "sync_adapters" not in workflow


@pytest.mark.parametrize("copy", [".claude/commands/qa.md", "src/torque/data/commands/qa.md",
                                  "src/torque/data/skills/salesforce-build/SKILL.md"])
def test_the_drift_check_fails_on_a_hand_edited_copy(tree, capsys, copy):
    assert run_check(tree, capsys)[0] == 0
    path = tree / copy
    path.write_bytes(path.read_bytes() + b"\nA line added by hand.\n")
    code, out = run_check(tree, capsys)
    assert code == 1 and f"{copy} (differs)" in out, out
    # Writing mode puts the generated text back, and the check passes again.
    assert SYNC.main(["--root", str(tree)]) == 0
    capsys.readouterr()
    assert run_check(tree, capsys)[0] == 0


@pytest.mark.parametrize("copy", [".claude/commands/extra.md", "src/torque/data/commands/extra.md"])
def test_the_drift_check_fails_on_a_stray_copy(tree, capsys, copy):
    (tree / copy).write_text("# /extra\n", encoding="utf-8")
    code, out = run_check(tree, capsys)
    assert code == 1 and f"{copy} (should not be there)" in out, out
    assert SYNC.main(["--root", str(tree)]) == 0 and not (tree / copy).exists()


@pytest.mark.parametrize("copy", [".claude/commands/qa.md", "src/torque/data/commands/qa.md",
                                  "src/torque/data/catalogue.json"])
def test_the_drift_check_fails_on_a_missing_copy(tree, capsys, copy):
    (tree / copy).unlink()
    code, out = run_check(tree, capsys)
    assert code == 1 and f"{copy} (missing)" in out, out


def test_the_drift_check_fails_on_a_missing_or_unlisted_source(tree, capsys):
    (tree / "workflows" / "qa.md").unlink()
    (tree / "workflows" / "extra.md").write_text("# /extra\n", encoding="utf-8")
    code, out = run_check(tree, capsys)
    assert code == 1, out
    assert "workflows/qa.md is missing" in out and "workflows/extra.md is not in workflows/catalogue.json" in out
    # A source problem stops the generator before it writes anything.
    before = (tree / ".claude" / "commands" / "qa.md").read_bytes()
    assert SYNC.main(["--root", str(tree)]) == 1
    assert (tree / ".claude" / "commands" / "qa.md").read_bytes() == before


def test_a_recipe_must_keep_the_catalogue_name_description_and_interface_line(tree, capsys):
    path = tree / "workflows" / "small-change.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("a new field,", "a field,", 1), encoding="utf-8", newline="\n")
    code, out = run_check(tree, capsys)
    assert code == 1 and "workflows/small-change.md does not start with its heading" in out, out
    path.write_text(text.replace("Guided recipe using existing tools.", "Native CLI with conversational "
                                 "interpretation.", 1), encoding="utf-8", newline="\n")
    assert run_check(tree, capsys)[0] == 1
