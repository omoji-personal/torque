from pathlib import Path

import torque
from torque import approval, delegation, doctor_connected

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "delegated-approver.md"


def test_doc_lists_every_probe_reason_class_and_path():
    text = DOC.read_text(encoding="utf-8")
    for route in doctor_connected.PROBE_ROUTES:
        assert f"`{route}`" in text, route
    for reason in ("agent-session", "not-delegated", "tier-2-required", "org-production-or-unknown",
                   "request-changed", "payload-changed", "request-denied", "consent-unusable",
                   "idempotency-conflict"):
        assert f"`{reason}`" in text, reason
    for pattern in (*approval.DELEGATED_READS, *approval.DELEGATED_WRITES):
        assert f"`{pattern}`" in text, pattern
    for patterns in delegation.SETUP_WRITES.values():
        for pattern in patterns:
            assert f"`{pattern}`" in text, pattern
    for code in ("0", "20", "21", "22"):
        assert f"| {code} |" in text


def test_doc_lists_every_reason_class_the_code_emits():
    """Every reason class a Refusal is raised with anywhere in the package, found by
    reading the source, so a new class cannot ship undocumented."""
    import re
    text = DOC.read_text(encoding="utf-8")
    found = set()
    for path in (ROOT / "src" / "torque").glob("*.py"):
        found |= set(re.findall(r'(?:Refusal|_refuse)\(\s*"([a-z0-9-]+)"', path.read_text(encoding="utf-8")))
    assert len(found) >= 18
    for reason in sorted(found):
        assert f"`{reason}`" in text, reason


def test_doc_shows_each_probe_answer_under_claude_p():
    text = " ".join(DOC.read_text(encoding="utf-8").split())
    for answer in set(doctor_connected.UNDER_P.values()):
        assert answer in text, answer


def test_version_is_alpha16():
    assert torque.__version__ == "2.0.0a16"
    assert 'version = "2.0.0a16"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_changelog_readme_and_record_for_alpha16():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    top = changelog.split("\n## ", 2)[1]
    assert top.startswith("2.0.0a16 - delegated approver, ")
    flat = " ".join(top.split())
    assert "torque approval permissions" in flat and "--write" in flat
    assert "approver_kind" in flat and "sf org open" in flat
    record = (ROOT / "docs" / "validation-alpha16.md").read_text(encoding="utf-8")
    assert "## Edited a15 tests" in record and "## Invariant verification" in record and "Round 0b" in record
    readme = (ROOT / "README.md").read_text(encoding="utf-8").split("\n## ", 1)[0]
    assert "2.0.0a16" in readme and "delegated-approver.md" in readme
    connected = (ROOT / "docs" / "connected-approval.md").read_text(encoding="utf-8")
    assert "delegated-approver.md" in connected
