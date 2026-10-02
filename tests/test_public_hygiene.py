"""Public source must stay firm-neutral."""
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from public_hygiene import denylist_patterns, nonreserved_emails, scan_text
# SHA-256 digests of the forbidden names (spaces, hyphens and case ignored), so this file names no firm.
FORBIDDEN = {
    "87f7ccd3e87428e0e237492b8a992d4272071929b9e3247ef47ed561c1468148",
    "b5481c7239dfa67ead224d9e13b652c7a058e7e7aedd1ce72af553b4e18a4fbe",
}
BINARY = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".whl", ".gz", ".zip", ".ico"}


def _names_firm(text):
    words = re.findall(r"[a-z]+", text)
    grams = set(words)
    grams.update(a + b for a, b in zip(words, words[1:]))
    grams.update(a + b + c for a, b, c in zip(words, words[1:], words[2:]))
    return any(hashlib.sha256(g.encode()).hexdigest() in FORBIDDEN for g in grams)


def test_tracked_text_names_no_firm():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    hits = []
    for name in files:
        path = ROOT / name
        if path.suffix.lower() in BINARY or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        if _names_firm(text):
            hits.append(name)
    assert hits == []


def test_profiles_are_neutral():
    from torque import workspace as ws
    assert ws.PROFILES == ("generic", "solution-lead")


def _tracked_text():
    files = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\0")
    for name in files:
        path = ROOT / name
        if path.suffix.lower() not in BINARY and path.is_file():
            yield name, path.read_text(encoding="utf-8", errors="replace")


def test_tracked_content_has_no_credentials_personal_paths_or_internal_urls():
    hits = [hit for name, text in _tracked_text() for hit in scan_text(name, text)]
    assert hits == []


def test_fictional_email_addresses_use_reserved_domains():
    hits = [name for name, text in _tracked_text()
            if ("tests" in Path(name).parts or "fixtures" in Path(name).parts)
            and nonreserved_emails(text)]
    assert hits == []


@pytest.mark.skipif(not os.environ.get("TORQUE_PRIVATE_DENYLIST"), reason="private denylist not configured")
def test_private_denylist():
    """Names that must never appear, read from a private file outside the repository
    (one term per line, # for comments), so this test names none of them."""
    patterns = denylist_patterns(Path(os.environ["TORQUE_PRIVATE_DENYLIST"]).expanduser())
    files = list(_tracked_text())
    hits = [name for name, text in files if any(pattern.search(text) for pattern in patterns)]
    if os.environ.get("TORQUE_TEST_PRIVATE_SCAN_STATUS"):
        Path(os.environ["TORQUE_TEST_PRIVATE_SCAN_STATUS"]).write_text(
            f"{'FAILED' if hits else 'PASSED'} ({len(files)} tracked text files)", encoding="utf-8")
    assert hits == []


def test_no_em_dashes_in_connected_mode_docs():
    for name in ("docs/connected-approval.md", "docs/validation-alpha15.md",
                 "docs/delegated-approver.md", "docs/validation-alpha16.md",
                 "src/torque/data/connected/production-approval.md"):
        assert "\u2014" not in (ROOT / name).read_text(encoding="utf-8"), name
