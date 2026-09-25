"""Packaged skills: well-formed, bundled, installed, listed and released."""
import fnmatch
import re
from importlib import resources
from pathlib import Path

import pytest

import torque
from torque import template_updates
from torque import workspace as ws

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".agents" / "skills"
BUNDLED = ROOT / "src" / "torque" / "data" / "skills"
NONPROFIT = ("salesforce-npsp", "salesforce-nonprofit-cloud")
ALL = ("salesforce-architecture-review", "salesforce-code-analyzer-review",
       "salesforce-soql-review", *NONPROFIT)


def _frontmatter(path):
    text = path.read_text(encoding="utf-8")
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert match, path
    fields = dict(line.split(": ", 1) for line in match.group(1).splitlines())
    return fields, text[match.end():]


def _skill_files(base):
    return sorted(p.relative_to(base).as_posix() for p in base.rglob("*.md"))


def test_every_skill_has_well_formed_frontmatter():
    assert sorted(p.name for p in SOURCE.iterdir() if p.is_dir()) == sorted(ALL)
    for name in ALL:
        fields, body = _frontmatter(SOURCE / name / "SKILL.md")
        assert set(fields) == {"name", "description"}, name
        assert fields["name"] == name and re.fullmatch(r"[a-z0-9-]{1,64}", name)
        assert 20 <= len(fields["description"]) <= 1024, name
        assert body.lstrip().startswith("# "), name


@pytest.mark.parametrize("name", NONPROFIT)
def test_nonprofit_skill_references_exist_and_are_linked(name):
    skill = (SOURCE / name / "SKILL.md").read_text(encoding="utf-8")
    references = sorted((SOURCE / name / "references").glob("*.md"))
    assert references, name
    for path in references:
        assert f"`references/{path.name}`" in skill, path
    for mentioned in re.findall(r"`(references/[a-z0-9-]+\.md)`", skill):
        assert (SOURCE / name / mentioned).is_file(), mentioned


@pytest.mark.parametrize("name", NONPROFIT)
def test_nonprofit_skill_text_is_plain_and_cites_official_docs(name):
    texts = [p.read_text(encoding="utf-8") for p in (SOURCE / name).rglob("*.md")]
    for text in texts:
        assert "\u2014" not in text
    joined = "\n".join(texts)
    assert "https://help.salesforce.com/" in joined or "https://developer.salesforce.com/" in joined
    assert "verify" in joined


def test_npsp_skill_covers_the_required_topics():
    text = "\n".join(p.read_text(encoding="utf-8") for p in (SOURCE / "salesforce-npsp").rglob("*.md"))
    for topic in ("TDTM", "Customizable rollups", "RD2", "npe01__OppPayment__c", "npsp__Allocation__c",
                  "Partial_Soft_Credit", "Gift Entry", "pmdm__ProgramEngagement__c",
                  "pmdm__ServiceDelivery__c", "pmdm__ServiceSession__c", "pmdm__ServiceSchedule__c",
                  "pmdm__ServiceParticipant__c", "feature gate", "outfunds",
                  "partially save", "DATEVALUE()", "no fault connector", "field-level security",
                  "subscriber copies of managed custom metadata", "rollback mode"):
        assert topic in text, topic


def test_nonprofit_cloud_skill_covers_the_required_topics():
    text = "\n".join(p.read_text(encoding="utf-8")
                     for p in (SOURCE / "salesforce-nonprofit-cloud").rglob("*.md"))
    for topic in ("GiftTransaction", "GiftCommitment", "Person Account", "PartyRelationshipGroup",
                  "Data Processing Engine", "GiftEntry", "Fundraising_Admin", "FundingAward",
                  "JobPositionShift", "IndicatorResult", "Deployable", "UI-only",
                  "## Object map", "## No equivalent", "## Sequence", "## Reconciliation",
                  "## Design-changing findings", "**verify**"):
        assert topic in text, topic


def test_bundled_copy_matches_the_source():
    assert _skill_files(SOURCE) == _skill_files(BUNDLED)
    for relative in _skill_files(SOURCE):
        assert (SOURCE / relative).read_bytes() == (BUNDLED / relative).read_bytes(), relative


def test_every_bundled_skill_file_is_packaged_in_the_wheel_and_sdist():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    line = re.search(r"^torque = \[(.*)\]$", text.split("[tool.setuptools.package-data]", 1)[1], re.M)
    patterns = re.findall(r'"([^"]+)"', line.group(1))
    for relative in _skill_files(BUNDLED):
        path = f"data/skills/{relative}"
        assert any(fnmatch.fnmatchcase(path, pattern) and pattern.count("/") == path.count("/")
                   for pattern in patterns), path
    assert "recursive-include .agents/skills *.md" in (ROOT / "MANIFEST.in").read_text(encoding="utf-8")


def test_workspace_init_and_upgrade_install_the_nonprofit_skills(tmp_path):
    root = ws.init_workspace(tmp_path / "firm", "Acme consulting")
    for base in (".claude/skills", ".agents/skills"):
        for name in NONPROFIT:
            for relative in _skill_files(SOURCE / name):
                target = root / base / name / relative
                assert target.read_bytes() == (SOURCE / name / relative).read_bytes(), target
    bundled = template_updates._bundled()
    for name in NONPROFIT:
        assert f".agents/skills/{name}/references/gotchas.md" in bundled
        assert f".claude/skills/{name}/SKILL.md" in bundled


def test_packaged_resources_include_the_skill_references():
    data = resources.files("torque").joinpath("data", "skills")
    for name in NONPROFIT:
        assert data.joinpath(name, "SKILL.md").is_file()
        assert data.joinpath(name, "references", "gotchas.md").is_file()


def test_skills_are_listed_in_the_docs():
    listing = (ROOT / "docs" / "skills.md").read_text(encoding="utf-8")
    for name in ALL:
        assert f"`{name}`" in listing, name
    assert "docs/skills.md" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/skills.md" in (ROOT / "AGENTS.md").read_text(encoding="utf-8")


def test_version_is_alpha17_or_later():
    assert re.fullmatch(r"2\.0\.0a(\d+)", torque.__version__) and int(torque.__version__[6:]) >= 17
    assert f'version = "{torque.__version__}"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_changelog_readme_and_record_for_alpha17():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    section = changelog.split("\n## 2.0.0a17 - ", 1)[1].split("\n## ", 1)[0]
    assert section.startswith("nonprofit knowledge skills, ")
    for name in NONPROFIT:
        assert f"`{name}`" in section
    record = (ROOT / "docs" / "validation-alpha17.md").read_text(encoding="utf-8")
    assert "## Review scope" in record and "Python 3." in record and "\u2014" not in record
    readme = (ROOT / "README.md").read_text(encoding="utf-8").split("\n## ", 1)[0]
    assert torque.__version__ in readme and "validation-alpha17.md" in readme
