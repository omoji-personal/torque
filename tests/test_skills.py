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
# Practice skills: a short body that says what to do first, depth in references/.
PRACTICE = ("salesforce-flow", "salesforce-build", "salesforce-delivery")
REVIEW = ("salesforce-architecture-review", "salesforce-code-analyzer-review", "salesforce-soql-review")
ALL = (*REVIEW, *PRACTICE, *NONPROFIT)
# Skills written to the current standard: a description that says when, sources at the end of every file.
CURRENT = (*PRACTICE, *REVIEW)
WITH_REFERENCES = ALL


def _frontmatter(path):
    text = path.read_text(encoding="utf-8")
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert match, path
    fields = dict(line.split(": ", 1) for line in match.group(1).splitlines())
    return fields, text[match.end():]


def _skill_files(base):
    return sorted(p.relative_to(base).as_posix() for p in base.rglob("*.md"))


def _text(name):
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted((SOURCE / name).rglob("*.md")))


def test_every_skill_has_well_formed_frontmatter():
    assert sorted(p.name for p in SOURCE.iterdir() if p.is_dir()) == sorted(ALL)
    for name in ALL:
        fields, body = _frontmatter(SOURCE / name / "SKILL.md")
        assert set(fields) == {"name", "description"}, name
        assert fields["name"] == name and re.fullmatch(r"[a-z0-9-]{1,64}", name)
        assert 20 <= len(fields["description"]) <= 1024, name
        # A host lists only the description: it has to say when to load the skill.
        assert "Use it " in fields["description"], name
        assert body.lstrip().startswith("# "), name


@pytest.mark.parametrize("name", CURRENT)
def test_skill_says_when_to_use_it_and_stays_short(name):
    fields, body = _frontmatter(SOURCE / name / "SKILL.md")
    description = fields["description"]
    # The description is what a host lists: it must say when to load the skill, and parse as plain YAML.
    assert "Use it " in description and len(description) < 1000, name
    assert ": " not in description and "`" not in description and " #" not in description, name
    # The body is read in the middle of a task: one screen of next actions, depth in references.
    assert len(body.strip("\n").splitlines()) <= 150, name
    for path in (SOURCE / name / "references").glob("*.md"):
        assert len(path.read_text(encoding="utf-8").strip("\n").splitlines()) <= 225, path


@pytest.mark.parametrize("name", WITH_REFERENCES)
def test_skill_references_exist_and_are_linked(name):
    skill = (SOURCE / name / "SKILL.md").read_text(encoding="utf-8")
    references = sorted((SOURCE / name / "references").glob("*.md"))
    assert references, name
    for path in references:
        assert f"`references/{path.name}`" in skill, path
    for mentioned in re.findall(r"`(references/[a-z0-9-]+\.md)`", skill):
        assert (SOURCE / name / mentioned).is_file(), mentioned


def test_pointers_between_skill_files_resolve():
    for path in sorted(SOURCE.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        # `salesforce-build/references/data-model.md` is relative to the skills folder.
        for pointer in re.findall(r"`(salesforce-[a-z-]+/references/[a-z0-9-]+\.md)`", text):
            assert (SOURCE / pointer).is_file(), (path, pointer)
        # skill `salesforce-flow` names another skill.
        for skill in re.findall(r"skills? `(salesforce-[a-z-]+)`", text):
            assert (SOURCE / skill / "SKILL.md").is_file(), (path, skill)
        # Inside references/, `gotchas.md` names a file beside the one being read.
        if path.parent.name == "references":
            for sibling in re.findall(r"`([a-z0-9-]+\.md)`", text):
                assert (path.parent / sibling).is_file(), (path, sibling)


@pytest.mark.parametrize("name", CURRENT)
def test_skill_files_cite_sources_and_stay_plain(name):
    for path in sorted((SOURCE / name).rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        assert chr(0x2014) not in text and chr(0x2013) not in text, path  # no long dashes
        sources = text.split("\n## Sources\n", 1)
        assert len(sources) == 2 and "\n## " not in sources[1], path  # sources come last
        assert "https://" in sources[1] or "field-reported" in sources[1], path
        # Unconfirmed statements are marked, and the mark is explained where it is used.
        if "**verify**" in sources[0]:
            assert "not confirmed in a fetched primary source" in _text(name), path


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
                  "subscriber copies of managed custom metadata",
                  # nonprofit lessons from the field record
                  "CampaignMember.AccountId", "Opening NPSP Settings can write"):
        assert topic in text, topic
    # The platform lessons that apply to every org moved to the general skills; the NPSP skill points
    # to them and test_flow_skill_covers_the_required_topics and test_build_skill_covers_the_required_topics
    # check that they arrived.
    skill = (SOURCE / "salesforce-npsp" / "SKILL.md").read_text(encoding="utf-8")
    for pointer in ("salesforce-flow/references/gotchas.md", "salesforce-build/references/access-and-sharing.md"):
        assert pointer in skill, pointer
    for moved in ("DATEVALUE()", "rollback mode"):
        assert moved not in text, moved


def test_flow_skill_covers_the_required_topics():
    skill = (SOURCE / "salesforce-flow" / "SKILL.md").read_text(encoding="utf-8")
    # The body carries the standard an agent applies to every flow, each rule with its check.
    for topic in ("before save", "entry conditions", "trigger order", "inside a loop", "fault path",
                  "Hard-code no", "running context", "Describe the flow", "200 records", "active version",
                  "torque advisory flow", "torque advisory impact", "torque browser login", "[check]"):
        assert topic in skill, topic
    text = _text("salesforce-flow")
    for topic in ("faultConnector", "triggerOrder", "runInMode", "FlowDefinitionView", "ActiveVersionId",
                  "--rule-selector flow", "MissingFaultHandler", "DbInLoop", "$Permission",
                  # general platform lessons that used to live only in the NPSP skill
                  "partially save", "DATEVALUE()", "no fault connector", "rollback mode",
                  # field lessons
                  "choice field's label", "<triggers>", "isAdditionalPermissionRequiredToRun", "Run Flows",
                  "storeOutputAutomatically", "Duplicate developer name", "by version"):
        assert topic in text, topic


def test_build_skill_covers_the_required_topics():
    skill = (SOURCE / "salesforce-build" / "SKILL.md").read_text(encoding="utf-8")
    # The body is the standard for every build: the owner's examples must be unmistakable in it.
    for topic in ("Reuse what exists", "Simplest option first", "Document what you build", "naming convention",
                  "Ship access with every new field", "Configuration instead of hard-coded values",
                  "client's admin can open and change", "managed-package components",
                  "Retrieve before you change", "as the intended user", "torque browser login", "[check]",
                  "Stop and ask first"):
        assert topic in skill, topic
    text = _text("salesforce-build")
    for topic in (
            # documentation and naming: what to write, where, limits, what never to write
            "inlineHelpText", "1,000 characters", "510 characters", "version number", "house standard",
            # access: the field-level security lesson that used to live only in the NPSP skill
            "field-level security", "fieldPermissions", "FieldPermissions", "PermissionSetGroupComponent",
            "UserRecordAccess", "torque advisory evidence",
            # configuration, data model, pages and reports
            "custom metadata", "custom permission", "named credential", "restricted picklist",
            "Dynamic Forms", "list view", "dashboard", "running user",
            # code and integration
            "with sharing", "API 67.0", "Assert", "upsert on an external Id", "Deliverability"):
        assert topic in text, topic


def test_delivery_skill_maps_every_phase_and_carries_the_field_lessons():
    skill = (SOURCE / "salesforce-delivery" / "SKILL.md").read_text(encoding="utf-8")
    # One screen: each phase with what matters most, the artifact it produces and the human sign-off.
    for phase in ("Take over or assess an org", "Discovery and requirements", "Solution design", "Environments",
                  "Testing and UAT", "Data migration", "Release", "Documentation, training, hand-over",
                  "Support and org health", "Estimating and scope"):
        assert f". {phase} |" in skill, phase
    for topic in ("| Produces |", "| Human sign-off |", "torque browser login", "torque session add",
                  'Say what "verified" rests on'):
        assert topic in skill, topic
    text = _text("salesforce-delivery")
    for topic in (
            # phase checklists and artifacts
            "acceptance criteria", "decision record", "options table", "build register",
            "manual-steps register", "leftover ledger", "data dictionary", "teach-back", "Reconcile",
            "Health Check", "Release Updates", "| 26 |",
            # the checks that would have caught escaped defects
            "non-admin", "one person per user group", "every record type that shares the page layout",
            "zero starting rows", "exact username", "Known open defects",
            "Inject a fault into any step you add to someone else's automation",
            # deploy and retrieve mechanics from failed commands
            "Check-only before every real deploy", "--target-metadata-dir",
            # what verified means, and documents
            "A check counts only if its result would differ when the thing is wrong",
            "Restate the request and get a yes", "one-page outline", "One vet, then post once",
            "A review loop converging is not the reader accepting"):
        assert topic in text, topic


def test_review_skills_carry_their_methods_and_traps():
    architecture = _text("salesforce-architecture-review")
    for topic in ("Trace one real transaction", "Fairness", "| A01 |", "| A40 |", "| 40 |",
                  "Stop-and-ask lists", "2026-09-14"):
        assert topic in architecture, topic
    soql = _text("salesforce-soql-review")
    for topic in ("Describe before you query", "Do not guess again", "--use-tooling-api",
                  "Disjunctions not supported", "groupable", "filterable", "--all-rows",
                  "An error means the query did not run", "caret line"):
        assert topic in soql, topic
    analyzer = _text("salesforce-code-analyzer-review")
    for topic in ("sf code-analyzer run", "--rule-selector flow", "MissingFaultHandler", "MissingDescription",
                  "Recommended", "--output-file", "What it does not cover", "Lightning Flow Scanner",
                  "never rebuild"):
        assert topic in analyzer, topic


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


def test_workspace_init_and_upgrade_install_every_skill_with_its_references(tmp_path):
    root = ws.init_workspace(tmp_path / "firm", "Acme consulting")
    for base in (".claude/skills", ".agents/skills"):
        for name in ALL:
            for relative in _skill_files(SOURCE / name):
                target = root / base / name / relative
                assert target.read_bytes() == (SOURCE / name / relative).read_bytes(), target
    bundled = template_updates._bundled()
    for name in ALL:
        assert f".claude/skills/{name}/SKILL.md" in bundled
        assert f".agents/skills/{name}/SKILL.md" in bundled
    for name in WITH_REFERENCES:
        for reference in (SOURCE / name / "references").glob("*.md"):
            assert f".agents/skills/{name}/references/{reference.name}" in bundled, reference
            assert f".claude/skills/{name}/references/{reference.name}" in bundled, reference


def test_packaged_resources_include_the_skill_references():
    data = resources.files("torque").joinpath("data", "skills")
    for name in WITH_REFERENCES:
        assert data.joinpath(name, "SKILL.md").is_file()
        for reference in (SOURCE / name / "references").glob("*.md"):
            assert data.joinpath(name, "references", reference.name).is_file(), reference


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
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert torque.__version__ in readme.split("\n## ", 1)[0]
    assert "validation-alpha17.md" in readme
