"""Recipes that create or remove records tell the consultant to save Ids at creation and delete only those."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CREATING = ("qa", "qa-browser", "qa-multiprofile", "probes", "prep-migration",
            "grants-outbound-funds", "verify-change", "validate-change")
RECIPES = (*CREATING, "undo")
COPIES = ("workflows", ".claude/commands", "src/torque/data/commands")


@pytest.mark.parametrize("copy", COPIES)
@pytest.mark.parametrize("name", RECIPES)
def test_recipe_deletes_only_recorded_ids(name, copy):
    text = " ".join((ROOT / copy / f"{name}.md").read_text(encoding="utf-8").split())
    if name == "undo":
        # Undo removes records an earlier step saved; it does not create them.
        assert "Ids saved when they were created" in text
    else:
        phrase = "save each created record's Id when it is created"
        assert phrase in text
        capture = text.index(phrase)
        assert "in the private task directory" in text[capture:capture + 120]
        assert "only those Ids" in text[capture:]
    assert "sf data delete bulk --file" in text
    assert "only those Ids" in text or "only the Ids" in text
    assert "name, title, creator or date" in text
    assert "stop and ask instead of matching" in text
