"""Content checks shared by source tests and wheel/sdist qualification.

Report locations and categories only; never echo a suspected secret or denylist
term. These targeted checks complement review, not general PII detection.
"""
import ipaddress
from pathlib import Path
import re
import tarfile
from urllib.parse import urlsplit
import zipfile


SECRETS = {
    "private key": re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"),
    "provider token": re.compile(
        r"\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36,}|"
        r"github_pat_[A-Za-z0-9_]{50,}|sk-(?:proj-|ant-)?[A-Za-z0-9_-]{32,}|"
        r"xox[baprs]-[A-Za-z0-9-]{24,}|00D[A-Za-z0-9]{12,15}![A-Za-z0-9._-]{60,})"),
    "literal credential": re.compile(
        r'''(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password)'''
        r'''["']?\s*[:=]\s*["']?([A-Za-z0-9_+/=-]{24,})(?=["'\s,;}]|$)'''),
    "URL credential": re.compile(r"https?://[^\s/@:]+:[^\s/@]+@"),
}
HOME_PATH = re.compile(
    r"(?<![\w:/])(?:/(?:Users|home)/|[A-Za-z]:[\\/]+Users[\\/]+)([\w.-]+)")
# Existing path-parser fixtures and documented OS/service-account placeholders.
HOME_PLACEHOLDERS = {"a", "u", "x", "APPROVER", "USER", "USERNAME", "user", "runner",
                     "example", "sample", "Public", "Default", "..."}
URL = re.compile(r"https?://[^\s<>\"'`)}\]]+")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")


def scan_text(name: str, text: str) -> list[str]:
    findings = []
    for category, pattern in SECRETS.items():
        for match in pattern.finditer(text):
            # Repeated characters are explicit shape fixtures, not usable keys.
            if category == "literal credential" and len(set(match.group(1))) < 4:
                continue
            findings.append((match.start(), category))
    for match in HOME_PATH.finditer(text):
        if match.group(1) not in HOME_PLACEHOLDERS:
            findings.append((match.start(), "personal absolute path"))
    for match in URL.finditer(text):
        if "{" in match.group():  # Source-code URL templates have no literal host.
            continue
        try:
            host = urlsplit(match.group()).hostname or ""
        except ValueError:
            continue
        if "tests" in Path(name).parts and host in {"x", "fake"}:
            continue  # Existing minimal URL-parser fixtures.
        if name.endswith("tests/test_connected_r9.py") and match.group() == "https:" + "//10.0.0.5/":
            continue  # Deliberately refused RFC1918 destination, never contacted.
        internal = host.endswith((".internal", ".corp", ".local", ".lan", ".intranet"))
        internal |= bool(host and "." not in host and ":" not in host and host != "localhost")
        try:
            address = ipaddress.ip_address(host)
            internal |= any(address in network for network in (
                ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"),
                ipaddress.ip_network("192.168.0.0/16"), ipaddress.ip_network("fc00::/7")))
        except ValueError:
            pass
        if internal:
            findings.append((match.start(), "internal URL"))
    return [f"{name}:{text.count(chr(10), 0, offset) + 1}: {category}"
            for offset, category in sorted(findings)]


def nonreserved_emails(text: str) -> list[str]:
    """Fictional addresses may use example domains or reserved test TLDs."""
    return [match.group(0) for match in EMAIL.finditer(text)
            if not (match.group(1).lower() in {"example.org", "example.com", "example.net"}
                    or match.group(1).lower().endswith((".example.org", ".example.com", ".example.net",
                                                       ".example", ".invalid", ".test")))]


def distribution_text(path: Path):
    """Inspect members without extracting archive paths or following links."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                if not member.is_dir():
                    yield member.filename, archive.read(member).decode("utf-8", errors="replace")
    else:
        with tarfile.open(path) as archive:
            for member in archive:
                if member.isfile():
                    with archive.extractfile(member) as stream:
                        yield member.name, stream.read().decode("utf-8", errors="replace")


def denylist_patterns(path: Path):
    terms = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    if not terms:
        raise ValueError("Private denylist contains no terms")
    return [re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", re.I) for term in terms]


def scan_distribution(path: Path, private_denylist: Path | None = None) -> list[str]:
    patterns = denylist_patterns(private_denylist) if private_denylist else []
    findings = []
    for name, text in distribution_text(path):
        findings.extend(scan_text(name, text))
        if any(pattern.search(text) for pattern in patterns):
            findings.append(f"{name}: private denylist match")
    return findings
