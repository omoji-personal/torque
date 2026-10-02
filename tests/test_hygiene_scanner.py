"""Synthetic regressions for source and artifact content inspection."""
import importlib.util
import io
from pathlib import Path
import sys
import tarfile
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from public_hygiene import nonreserved_emails, scan_distribution, scan_text


CASES = [
    ("-----BEGIN " + "RSA PRIVATE KEY-----", "private key"),
    ("ghp_" + "Ab12" * 10, "provider token"),
    ('api_key="' + "Ab12Cd34" * 5 + '"', "literal credential"),
    ("export API_KEY=" + "Ab12Cd34" * 5, "literal credential"),
    ("/Users/" + "synthetic-consultant/work", "personal absolute path"),
    ("/home/" + "synthetic-consultant/work", "personal absolute path"),
    ("C:\\Users\\" + "synthetic-consultant\\work", "personal absolute path"),
    ("https:" + "//records.internal/", "internal URL"),
    ("https:" + "//10.1.2.3/private", "internal URL"),
    ("https:" + "//user:synthetic-password@example.org/", "URL credential"),
]


@pytest.mark.parametrize("text,category", CASES)
def test_scanner_detects_sensitive_shapes_without_echoing_contents(text, category):
    hits = scan_text("fixture.txt", text)
    assert hits == [f"fixture.txt:1: {category}"]
    assert text not in "\n".join(hits)


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
@pytest.mark.parametrize("text,category", CASES)
def test_distribution_scans_member_content(tmp_path, kind, text, category):
    artifact = _archive(tmp_path, kind, text)
    assert scan_distribution(artifact) == [f"artifact/notes.txt:1: {category}"]


def _archive(tmp_path, kind, text):
    artifact = tmp_path / ("candidate.whl" if kind == "wheel" else "candidate.tar.gz")
    if kind == "wheel":
        with zipfile.ZipFile(artifact, "w") as archive:
            archive.writestr("artifact/notes.txt", text)
    else:
        with tarfile.open(artifact, "w:gz") as archive:
            member = tarfile.TarInfo("artifact/notes.txt")
            data = text.encode()
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return artifact


def test_distribution_command_invokes_content_scan(tmp_path, monkeypatch):
    artifact = _archive(tmp_path, "wheel", CASES[0][0])
    spec = importlib.util.spec_from_file_location("distribution_check", ROOT / "scripts/check-distribution.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", ["check-distribution.py", str(artifact)])
    with pytest.raises(SystemExit, match="Public-content scan failed.*"):
        module.main()


def test_private_denylist_also_checks_distributions(tmp_path):
    terms = tmp_path / "terms.txt"
    terms.write_text("private-synthetic-canary\n", encoding="utf-8")
    artifact = _archive(tmp_path, "wheel", "private-synthetic-canary")
    assert scan_distribution(artifact, terms) == ["artifact/notes.txt: private denylist match"]


def test_required_public_notices_and_provenance_remain_allowed():
    for name in ("LICENSE", "NOTICE", "pyproject.toml", "packages/provenance.json", "docs/package-migration.md"):
        assert scan_text(name, (ROOT / name).read_text(encoding="utf-8")) == []


def test_only_reserved_email_domains_are_valid_for_fictional_data():
    assert nonreserved_emails("sample@example.org sample@example.com sample@org.example") == []
    assert nonreserved_emails("sample@" + "ordinary-domain.com")
