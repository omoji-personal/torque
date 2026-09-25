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


def test_edited_a15_tests_reconciliation_matches_git():
    """V2-4: the "Edited a15 tests" count and file list match
    `git diff --name-only dea2041 -- tests/` restricted to files that existed at
    dea2041. Skipped when git or the dea2041 commit is not available (an sdist or
    a shallow clone)."""
    import re
    import shutil
    import subprocess
    import pytest
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not available")

    def run(*args):
        return subprocess.run([git, "-C", str(ROOT), *args], capture_output=True, text=True, timeout=30,
                              stdin=subprocess.DEVNULL)
    if run("cat-file", "-e", "dea2041^{commit}").returncode != 0:
        pytest.skip("the dea2041 baseline commit is not in this checkout")
    baseline = run("ls-tree", "-r", "--name-only", "dea2041", "--", "tests/")
    changed = run("diff", "--name-only", "dea2041", "--", "tests/")
    if baseline.returncode != 0 or changed.returncode != 0:
        pytest.skip("git could not compare against dea2041")
    edited = sorted(set(changed.stdout.split()) & set(baseline.stdout.split()))
    record = (ROOT / "docs" / "validation-alpha16.md").read_text(encoding="utf-8")
    section = " ".join(record.split("## Edited a15 tests", 1)[1].split("\n## ", 1)[0].split())
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
             "nine": 9, "ten": 10}
    match = re.search(r"(\w+) files that existed at `dea2041` differ", section)
    assert match, "the reconciliation sentence is missing"
    assert words.get(match.group(1).lower()) == len(edited), (match.group(1), edited)
    for name in edited:
        assert f"`{name}" in section, name
