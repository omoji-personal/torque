"""What may not be saved in a lesson, checked when it is captured.

Every scope:
  refused  credential-looking values: session and access tokens, sign-in addresses that
           carry one (front door, auth URL), authorization headers, private keys, provider
           tokens, and a value after an explicit secret label (password, passcode, secret,
           token, API key, client secret, session id), in an assignment or in prose
  masked   email addresses, phone numbers, social security numbers, Salesforce user, record and
           org Ids; invisible characters are removed

Firm scope, because a firm lesson is shown for every client:
  refused  the names, slugs and org aliases of this workspace's clients, org Ids and org
           host names

A refusal names the class of what was found (with `--json`, also how often), never the
value. This is a heuristic, not proof that a text is safe to share: it knows shapes and
this workspace's client list, not every name a person could write or every way to write a
secret (docs/lessons.md lists what it lets through).
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
    ("authorization header", re.compile(r"(?<!Authorization: )\bBearer\s+([A-Za-z0-9._~+/=\-]{20,})"), 1),
    ("private key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"), None),
    ("provider token", re.compile(
        r"\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,}"
        r"|sk-(?:proj-|ant-)?[A-Za-z0-9_\-]{32,}|xox[baprs]-[A-Za-z0-9\-]{24,}"
        r"|eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})"), None),
)

# The value of an Authorization header, whatever its scheme (Basic, Bearer, Token, ...) and
# whatever it is made of: the header says it is a credential.
_HEADER = re.compile(r"(?i)\bAuthorization\s*:\s*(?:(?:Basic|Bearer|Token|Digest|OAuth|Negotiate)\s+)?"
                     r"([A-Za-z0-9._~+/=\-]{8,})")

# A value written after an explicit secret label. The label decides, not what the value
# is made of: `password=onlyletters` and `The password is "X"` are refused alike.
_LABEL = (r"(?:pass(?:word|wd|phrase|code)|client[ _-]?secret|consumer[ _-]?secret|secret|api[ _-]?key"
          r"|(?:access|refresh|security|auth|session)[ _-]?token|token|session[ _-]?id|sfdx[ _-]?auth[ _-]?url)")
_VALUE = r"(?:\"(?P<dq>[^\"\n]{1,200})\"|'(?P<sq>[^'\n]{1,200})'|`(?P<bq>[^`\n]{1,200})`|(?P<bare>[^\s\"'`,;<>]+))"
_START = r"(?i)(?<![A-Za-z0-9:])"             # not inside a command name such as force:user:password:generate
_LABELLED = (
    # password=..., "api_key": "...", password:value, sid=...
    ("assignment", re.compile(_START + r"(?:" + _LABEL + r"|pwd|sid)[\"']?[ \t]*(?:=|:(?=\S))[ \t]*" + _VALUE)),
    # --password VALUE, --client-secret=VALUE
    ("assignment", re.compile(r"(?i)(?<![A-Za-z0-9-])--(?:password|passwd|passcode|secret|client-secret|token"
                              r"|access-token|api-key)(?:[ \t]+|=)" + _VALUE)),
    # password: value
    ("prose", re.compile(_START + r"(?:" + _LABEL + r"|pwd)[\"']?[ \t]*:[ \t]+" + _VALUE)),
    # The password is "X"; the API key for the sync user was X; the token was set to X
    ("prose", re.compile(_START + _LABEL + r"(?:[ \t]+(?:for|of)[ \t]+[^\n.:;\"'`]{1,40}?)?[ \t]+"
                         r"(?:(?:(?:is|was)[ \t]+)?(?:set|changed|reset)[ \t]+to|is|was|were|are|reads|becomes"
                         r"|became)[ \t]+" + _VALUE)),
)
_WORDLIKE = re.compile(r"[A-Za-z]+(?:[-/][A-Za-z]+)*")
_VARIABLE = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+")      # SF_PASSWORD: the name of where it is kept


def _placeholder(value: str) -> bool:
    """A stand-in, not a value: <token>, $TOKEN, %TOKEN%, {{token}}, ****, xxxx, ..., an
    environment variable's name, or a word such as `required` or `redacted`."""
    return (not value or value[0] in "<${%*[(" or value.lower() in _PLACEHOLDERS or len(set(value.lower())) <= 1
            or value in ("...", "…") or bool(_VARIABLE.fullmatch(value)))


def _labelled_secrets(text: str) -> int:
    """How many distinct values follow an explicit secret label. Quoted, or in an
    assignment, any value counts (four characters or more unquoted). Unquoted in prose
    (`the token is short-lived`), a value counts when it does not read as a word: six
    characters or more with a digit or a symbol, or twenty or more of anything."""
    spans: list[tuple[int, int]] = []
    for form, pattern in _LABELLED:
        for match in pattern.finditer(text):
            group = next(name for name in ("dq", "sq", "bq", "bare") if match.group(name) is not None)
            start, end = match.span(group)
            if any(start < other_end and other_start < end for other_start, other_end in spans):
                continue
            if text[max(0, match.start() - 16):match.start()].lower().rstrip().endswith("unexpected"):
                continue                          # a parser's "unexpected token: 'ALL'", not a credential
            value = match.group(group).strip()
            if group != "bare":
                refused = not _placeholder(value) and len(value.split()) <= 4    # more words: a sentence in quotes
            else:
                value = value.rstrip(".,;:)!?")
                if _placeholder(value):
                    refused = False
                elif form == "assignment":
                    refused = len(value) >= 4
                else:
                    refused = len(value) >= 20 or (len(value) >= 6 and not _WORDLIKE.fullmatch(value))
            if refused:
                spans.append((start, end))
    return len(spans)

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
# Older shapes: an instance host (na139.salesforce.com), and any name under force.com or
# documentforce.com (sites, Visualforce and file hosts). Salesforce's own public hosts
# (login, test, developer, help, ...) are not an org's.
_OLD_HOST = re.compile(
    r"(?<![A-Za-z0-9.\-])(?:((?:na|cs|eu|ap|um|usa|gs)\d{1,3})(\.salesforce\.com)"
    r"|([A-Za-z0-9][A-Za-z0-9\-]*)((?:\.[A-Za-z0-9\-]+)*\.(?:document)?force\.com))(?![A-Za-z0-9\-])", re.I)
_HOST_PLACEHOLDERS = {"mydomain", "mydomainname", "yourdomain", "your-domain", "example", "domain", "orgname",
                      "yourorg", "your-org", "company", "yourcompany", "myorg", "my-org"}
_PUBLIC_HOSTS = {"login", "test", "developer", "developers", "help", "www", "trailhead", "appexchange", "lightning",
                 "partners", "success", "status", "trust", "api", "admin"}

WITHHELD = "(withheld: it holds a credential-looking value; open the file to read it)"
# A custom object, field, metadata type or event name. Not refused (a firm lesson may name a
# field), but a promotion lists them, since a client's own field can identify it.
CUSTOM_NAME = re.compile(r"\b[A-Za-z][A-Za-z0-9_]*?__(?:c|r|mdt|e|x|b)\b")
GENERAL = {"client name": "the client", "client slug": "the client", "org alias": "the org",
           "initiative name": "the initiative", "org Id": "<org-id>", "org host name": "<org-host>"}
FIRM_ONLY = tuple(GENERAL)


_KEYED = {"00D": "org Id", "005": "user Id"}
_CHECKSUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"


def _checksum_holds(token: str) -> bool:
    """An 18-character Id ends in three characters computed from the case of the first 15."""
    return len(token) == 18 and all(
        _CHECKSUM[sum(1 << bit for bit, char in enumerate(token[part * 5:part * 5 + 5]) if "A" <= char <= "Z")]
        == token[15 + part] for part in range(3))


def _keyed_id(token: str) -> str | None:
    """High confidence: an org or a user Id, known by its key prefix and its length (15
    or 18), whatever the rest is made of. `00DXXXXXXXXXXXX`, a placeholder, is not one."""
    kind = _KEYED.get(token[:3])
    return kind if kind and len(set(token[3:15])) > 1 else None


def _looks_like_record_id(token: str) -> bool:
    """The broader heuristic for other record Ids, which have no fixed prefix: zeros where
    the record number is padded (001A000001AbCdE) and digits beside letters, or an 18-
    character token whose checksum holds. A camel-case word, a hash or a version number is
    neither, so an ordinary 15-letter word is left alone."""
    mixed = sum(c.isdigit() for c in token) >= 3 and any(c.isalpha() for c in token)
    return mixed and ("00" in token[5:10] or _checksum_holds(token))


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
        return (f"Not saved in {scope} scope: the text identifies a client or an initiative of this workspace "
                f"({', '.join(firm)}). "
                "A firm lesson is shown for every client and initiative. Rewrite it without that (say `the "
                "client`), or keep it with the client or initiative by leaving out --scope firm.")


def workspace_terms(workspace: Path | None) -> list[tuple[str, re.Pattern]]:
    """Patterns for the names, slugs and org aliases of this workspace's clients and the slugs of its
    initiatives, read from each one's own registration (client.json, binding.json) and nothing else."""
    terms: list[tuple[str, re.Pattern]] = []
    seen: set[str] = set()
    if workspace is None:
        return terms

    def registration(folder: Path, name: str) -> dict:
        try:
            data = json.loads((folder / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        return data if isinstance(data, dict) else {}

    found = []
    for parent, marker in (("clients", "client.json"), ("initiatives", "binding.json")):
        folders = sorted(path for path in (workspace / parent).iterdir() if path.is_dir()) \
            if (workspace / parent).is_dir() else []
        for folder in folders:
            data = registration(folder, marker)
            if parent == "clients":
                found += [("client name", data.get("name")), ("client slug", data.get("slug") or folder.name),
                          ("org alias", data.get("org"))]
            else:
                # An initiative's registration holds its slug; the slug pattern also matches the name
                # written with spaces (internal-playbook matches Internal Playbook).
                found.append(("initiative name", data.get("slug") or folder.name))
    for kind, value in found:
        value = value.strip() if isinstance(value, str) else ""
        if len(value) < 2:
            continue        # one character would refuse every firm lesson with the word "a" or "I"
        if len(value) < 3:
            # Two characters: as registered and in capitals, on word boundaries. A name XY matches XY, not
            # "Xy"; its slug xy matches xy and XY.
            pattern, flags = "|".join(dict.fromkeys(re.escape(form) for form in (value, value.upper()))), 0
        else:
            # A name matches however its words are joined: Sample Nonprofit, sample-nonprofit, sample_nonprofit.
            pattern, flags = r"[\s_\-]*".join(re.escape(part) for part in re.split(r"[\s_\-]+", value.lower())
                                              if part), re.I
        if (pattern, flags) not in seen:
            seen.add((pattern, flags))
            terms.append((kind, re.compile(r"(?<![A-Za-z0-9])(?:" + pattern + r")(?![A-Za-z0-9])", flags)))
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
            least = 6 if name == "address with a password" else 12
            if value_group is None or _secret_like(match.group(value_group), least):
                count(found.refused, name)
    headers = sum(not _placeholder(match.group(1)) for match in _HEADER.finditer(text))
    if headers:
        count(found.refused, "authorization header", headers)
    labelled = _labelled_secrets(text)
    if labelled:
        count(found.refused, "password or secret", labelled)
    firm = firm_terms is not None

    def firm_hit(name: str) -> str | None:
        """Count a firm-only find; the replacement when generalizing, else None (keep the text)."""
        count(found.masked if generalize else found.refused, name)
        return GENERAL[name] if generalize else None

    def salesforce_id(match: re.Match) -> str:
        token = match.group()
        keyed = _keyed_id(token)
        if keyed == "org Id":
            if firm:
                return firm_hit("org Id") or token
            count(found.masked, "org Id")
            return "<org-id>"
        if keyed == "user Id":
            count(found.masked, "user Id")
            return "<user-id>"
        if not _looks_like_record_id(token):
            return token
        count(found.masked, "record Id")
        return "<record-id>"

    def host(match: re.Match) -> str:
        if match.group(1).lower() in _HOST_PLACEHOLDERS:
            return match.group()
        return firm_hit("org host name") or match.group()

    def old_host(match: re.Match) -> str:
        if match.group(1) is None and match.group(3).lower() in _HOST_PLACEHOLDERS | _PUBLIC_HOSTS:
            return match.group()
        if _ORG_HOST.fullmatch(match.group()):
            return match.group()             # counted already, as a current host shape
        return firm_hit("org host name") or match.group()

    if firm:
        text = _ORG_HOST.sub(host, text)
        text = _OLD_HOST.sub(old_host, text)
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
