from pathlib import Path
import re

import torque

REPO = Path(__file__).resolve().parents[1]


def test_version_is_alpha15_or_later():
    assert re.fullmatch(r"2\.0\.0a(\d+)", torque.__version__) and int(torque.__version__[6:]) >= 15
    assert f'version = "{torque.__version__}"' in (REPO / "pyproject.toml").read_text(encoding="utf-8")


def test_changelog_readme_and_record_for_alpha15():
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "\n## 2.0.0a15 - " in changelog
    record = (REPO / "docs" / "validation-alpha15.md").read_text(encoding="utf-8")
    assert "## Review scope" in record and "Python 3." in record
    top = (REPO / "README.md").read_text(encoding="utf-8").split("\n## ", 1)[0]
    assert torque.__version__ in top and "connected mode" in top


def test_connected_docs_state_the_tier_one_limit():
    text = " ".join((REPO / "docs" / "connected-approval.md").read_text(encoding="utf-8").split())
    assert "can read the key and forge an approval" in text
    assert "Tier 2, `owner-uid` (recommended for stage 2)" in text
    access = " ".join((REPO / "docs" / "ai-access.md").read_text(encoding="utf-8").split())
    assert "It has three values" in access and "connected-approval.md" in access
