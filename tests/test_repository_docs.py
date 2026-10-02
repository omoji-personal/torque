"""Contributor and first-use documentation contracts from the public audit."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_recipe_editing_instructions_name_sources_and_generation_order():
    instructions = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    rule = next(line for line in instructions.splitlines() if line.startswith("- Edit recipes"))
    assert "`workflows/*.md`" in rule
    assert rule.index("python workflows/sync_adapters.py") < rule.index("python scripts/sync-workflows.py")
    assert "Generated workflow adapters" in instructions


def test_readme_demonstrates_value_and_limits_before_control_details():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    install = readme.index("## Install and try")
    for text in ("**Before:**", "**After:**", "| Capability | Practical limit |",
                 "development alpha", "not a universal undo or Salesforce backup"):
        assert readme.index(text) < install
    assert install < readme.index("[delegated approver]")
    assert "git checkout --detach REVIEWED_COMMIT_SHA" in readme
    assert "git rev-parse HEAD" in readme


def test_validation_measured_results_precede_review_process():
    record = (ROOT / "docs/validation-alpha19.md").read_text(encoding="utf-8")
    assert record.index("3,550 tests") < record.index("five rounds of adversarial review")
