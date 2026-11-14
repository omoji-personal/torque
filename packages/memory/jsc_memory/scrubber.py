"""What may not be saved in a lesson, checked when it is captured.

Every scope:
  refused  credential-looking values: session and access tokens, sign-in addresses that
           carry one (front door, auth URL), authorization headers, private keys, provider
           tokens, `password=...` style assignments
  masked   email addresses, phone numbers, social security numbers, Salesforce record and
           org Ids; invisible characters are removed

Firm scope, because a firm lesson is shown for every client:
  refused  the names, slugs and org aliases of this workspace's clients, org Ids and org
           host names

A refusal names the class of what was found (with `--json`, also how often), never the
value. This is an aid, not proof that a text is safe to share: it knows shapes and this
workspace's client list, not every name a person could write.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re

from .lesson import APPLIES, Lesson, LessonError

# A value that reads like a real secret, not a placeholder such as <token>, $TOKEN or PASSWORD.
_PLACEHOLDERS = {"password", "passwd", "secret", "token", "none", "null", "true", "false", "redacted", "changeme",
                 "example", "value", "required", "optional", "string", "hidden", "masked", "yourpassword"}


def _secret_like(value: str, least: int = 12) -> bool:
    value = value.strip("\"'`.,;)")
    if len(value) < least or value[:1] in "<${%*[(" or value.lower() in _PLACEHOLDERS or len(set(value)) < 5:
        return False
    kinds = (any(c.isdigit() for c in value), any(c.islower() for c in value), any(c.isupper() for c in value),
             any(not c.isalnum() for c in value))
    return sum(kinds) >= 2


_URL_VALUE = r"([A-Za-z0-9._~!%+\-]{6,})"
_CREDENTIALS = (
    ("session token", re.compile(r"\b00D[A-Za-z0-9]{12,15}![A-Za-z0-9._\-]{20,}"), None),
    ("sign-in address", re.compile(r"\bforce://[^\s\"'<>]{12,}"), None),
    ("sign-in address", re.compile(r"frontdoor\.jsp\?[^\s\"'<>]*?\b(?:sid|otp)=" + _URL_VALUE, re.I), 1),
    ("sign-in address", re.compile(
        r"https?://[^\s\"'<>]*[?&#](?:sid|session_?id|access_token|refresh_token|token)=" + _URL_VALUE, re.I), 1),
    ("address with a password", re.compile(r"https?://[^\s/@:]+:([^\s/@]{3,})@"), 1),
    ("authorization header", re.compile(r"\bBearer\s+([A-Za-z0-9._~+/=\-]{20,})"), 1),
    ("private key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"), None),
    ("provider token", re.compile(
        r"\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,}"
        r"|sk-(?:proj-|ant-)?[A-Za-z0-9_\-]{32,}|xox[baprs]-[A-Za-z0-9\-]{24,}"
        r"|eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})"), None),
    ("password or secret", re.compile(
        r"(?i)\b(?:password|passwd|pwd|passphrase|secret|client[_-]?secret|consumer[_-]?secret|api[_-]?key"
        r"|access[_-]?token|refresh[_-]?token|security[_-]?token|auth[_-]?token|sfdx[_-]?auth[_-]?url)"
        r"\b[\"']?\s*[:=]\s*[\"']?([^\s\"',;}]{6,})"), 1),
)

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?:\+?\d{1,3}[\s.\-])?\(\d{3}\)\s?\d{3}[\s.\-]?\d{4}|(?<![\d.])\d{3}[\s.\-]\d{3}[\s.\-]\d{4}(?![\d.])")
_SSN = re.compile(r"(?<![\d\-])\d{3}-\d{2}-\d{4}(?![\d\-])")
# 15 or 18 letters and digits. A real Id has zeros where its record number is padded
# (001A000001AbCdE), which a camel-case word, a hash or a version number does not.
_SF_ID = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?(?![A-Za-z0-9_])")
# Soft hyphen, zero-width and direction marks, word joiners, the byte order mark.
_INVISIBLE = re.compile("[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
_ORG_HOST = re.compile(
    r"(?<![A-Za-z0-9.\-])([A-Za-z0-9][A-Za-z0-9\-]*)((?:\.[a-z]+)?\.(?:my\.salesforce(?:-setup|-sites)?\.com"
    r"|lightning\.force\.com|my\.site\.com|vf\.force\.com|visualforce\.com|cloudforce\.com))(?![A-Za-z0-9\-])", re.I)
_HOST_PLACEHOLDERS = {"mydomain", "mydomainname", "yourdomain", "your-domain", "example", "domain", "orgname",
                      "yourorg", "your-org", "company", "yourcompany", "myorg", "my-org"}

GENERAL = {"client name": "the client", "client slug": "the client", "org alias": "the org",
           "org Id": "<org-id>", "org host name": "<org-host>"}
FIRM_ONLY = tuple(GENERAL)


def _is_sf_id(token: str) -> bool:
    return ("00" in token[5:10] and sum(c.isdigit() for c in token) >= 3 and any(c.isalpha() for c in token))


@dataclass
class Found:
    """What a scrub did to one text or one lesson: classes masked and classes that refuse it."""
    masked: dict = field(default_factory=dict)
    refused: dict = field(default_factory=dict)

    def add(self, other: "Found") -> None:
        for mine, theirs in ((self.masked, other.masked), (self.refused, other.refused)):
            for name, count in theirs.items():
                mine[name] = mine.get(name, 0) + count

    def refusal(self, scope: str) -> str:
        """Why the lesson was not saved and what to do, naming classes and counts only."""
        firm = [name for name in self.refused if name in FIRM_ONLY]
        secret = [name for name in self.refused if name not in FIRM_ONLY]
        if secret:
            return (f"Not saved: the text holds a credential-looking value ({', '.join(secret)}). A lesson is "
                    "shown to agents again and again, so it must hold no secret. Remove the value (say where it "
                    "is kept instead) and save the lesson again.")
        return (f"Not saved in {scope} scope: the text identifies a client of this workspace ({', '.join(firm)}). "
                "A firm lesson is shown for every client. Rewrite it without that (say `the client`), or keep "
                "it with the client by leaving out --scope firm.")


def workspace_terms(workspace: Path | None) -> list[tuple[str, re.Pattern]]:
    """Patterns for the names, slugs and org aliases of this workspace's clients, read
    from each client's own registration (client.json) and nothing else in its folder."""
    terms: list[tuple[str, re.Pattern]] = []
    seen: set[str] = set()
    clients = workspace / "clients" if workspace else None
    if clients is None or not clients.is_dir():
        return terms
    for folder in sorted(clients.iterdir()):
        try:
            data = json.loads((folder / "client.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        for kind, value in (("client name", data.get("name")), ("client slug", data.get("slug") or folder.name),
                            ("org alias", data.get("org"))):
            if isinstance(value, str) and len(value.strip()) >= 3:
                # A name matches however its words are joined: Sample Nonprofit, sample-nonprofit, sample_nonprofit.
                pattern = r"[\s_\-]*".join(re.escape(part) for part in re.split(r"[\s_\-]+", value.strip().lower())
                                           if part)
                if pattern not in seen:
                    seen.add(pattern)
                    terms.append((kind, re.compile(r"(?<![A-Za-z0-9])" + pattern + r"(?![A-Za-z0-9])", re.I)))
    # Longest first, so a name or an alias is found whole before the slug inside it.
    return sorted(terms, key=lambda term: -len(term[1].pattern))


def scrub(text: str, *, firm_terms: list | None = None, generalize: bool = False) -> tuple[str, Found]:
    """The text as it may be saved, and what was found in it. `firm_terms` (from
    workspace_terms) switches the firm checks on. With `generalize`, firm-only finds are
    replaced with neutral words instead of refusing: the draft of a client lesson's firm copy."""
    found = Found()
    if not text:
        return text, found

    def count(group: dict, name: str, number: int = 1) -> None:
        group[name] = group.get(name, 0) + number

    text, removed = _INVISIBLE.subn("", text)
    if removed:
        count(found.masked, "invisible characters", removed)
    for name, pattern, value_group in _CREDENTIALS:
        for match in pattern.finditer(text):
            least = 6 if name in ("password or secret", "address with a password") else 12
            if value_group is None or _secret_like(match.group(value_group), least):
                count(found.refused, name)
    firm = firm_terms is not None

    def firm_hit(name: str) -> str | None:
        """Count a firm-only find; the replacement when generalizing, else None (keep the text)."""
        count(found.masked if generalize else found.refused, name)
        return GENERAL[name] if generalize else None

    def salesforce_id(match: re.Match) -> str:
        token = match.group()
        if not _is_sf_id(token):
            return token
        if token.startswith("00D"):
            if firm:
                return firm_hit("org Id") or token
            count(found.masked, "org Id")
            return "<org-id>"
        count(found.masked, "record Id")
        return "<record-id>"

    def host(match: re.Match) -> str:
        if match.group(1).lower() in _HOST_PLACEHOLDERS:
            return match.group()
        return firm_hit("org host name") or match.group()

    if firm:
        text = _ORG_HOST.sub(host, text)
        for name, pattern in firm_terms:
            text = pattern.sub(lambda match, name=name: firm_hit(name) or match.group(), text)
    text = _SF_ID.sub(salesforce_id, text)
    for name, pattern, mask in (("email address", _EMAIL, "<email>"), ("social security number", _SSN, "<ssn>"),
                                ("phone number", _PHONE, "<phone>")):
        text, number = pattern.subn(mask, text)
        if number:
            count(found.masked, name, number)
    return text, found


_EXTRA_DEPTH = 8


def scrub_lesson(item: Lesson, *, firm_terms: list | None = None, generalize: bool = False) -> Found:
    """Scrub every text of a lesson in place and report what was found: its fields, its
    evidence and the front-matter keys Torque does not know (names and values). A copy
    for a wider scope leaves those keys out before this runs (promote.propose)."""
    total = Found()

    def clean(value: str) -> str:
        value, found = scrub(value, firm_terms=firm_terms, generalize=generalize)
        total.add(found)
        return value

    def walk(value, depth: int = 0):
        """A front-matter value Torque does not know: every text in it, keys included."""
        if depth > _EXTRA_DEPTH:
            raise LessonError("a front-matter key Torque does not know holds values nested too deeply to check; "
                              "flatten it, or remove it from the file.")
        if isinstance(value, str):
            return clean(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            text = str(value)
            return value if clean(text) == text else clean(text)
        if isinstance(value, list):
            return [walk(part, depth + 1) for part in value]
        if isinstance(value, dict):
            return {walk(key, depth + 1): walk(part, depth + 1) for key, part in value.items()}
        return value

    for name in ("title", "when", "do", "because", "body"):
        setattr(item, name, clean(getattr(item, name)))
    for key in APPLIES:
        item.applies[key] = [clean(value) for value in item.applies.get(key, [])]
    for entry in item.evidence:
        if entry.get("ref"):
            entry["ref"] = clean(entry["ref"])
    item.extra = walk(item.extra)
    return total
