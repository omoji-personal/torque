"""Findings, the rule context and the text patterns the rules share."""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from .components import Component, Project

SEVERITIES = ("error", "warning", "note")
RANK = {s: i for i, s in enumerate(SEVERITIES)}

# A 15 or 18 character record Id: key prefix (3), pod (2), a zero, then the record number. Requiring the zero in
# position 6 and at least three digits keeps ordinary words and API names out. An 18 character Id must carry the
# case checksum Salesforce appends; a 15 character one must start with a digit or "a" (most prefixes), or with a
# prefix of the custom metadata record (m0) or knowledge article (kA, ka) kind.
ID_RE = re.compile(r"(?<![A-Za-z0-9_])([0-9a-zA-Z][a-zA-Z0-9]{4}0[a-zA-Z0-9]{9}(?:[a-zA-Z0-9]{3})?)(?![A-Za-z0-9_])")
CHECKSUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
TIMESTAMP = re.compile(r"\d{8,}T\d")
KEY_PREFIXES = {"005": "User", "00G": "Group or queue", "012": "Record type", "00e": "Profile",
                "0PS": "Permission set", "00D": "Organization", "001": "Account", "003": "Contact",
                "006": "Opportunity", "00Q": "Lead", "500": "Case", "701": "Campaign", "00B": "List view",
                "01Z": "Dashboard", "00O": "Report", "015": "Document", "00X": "Email template",
                "kA0": "Knowledge article", "ka0": "Knowledge article version"}
# Name parts that end a custom API name; any other "__" in a component's own name is a namespace.
NAME_SUFFIX = re.compile(r"__(c|mdt|e|b|x|r|pc|s|Share|History|Feed|ChangeEvent)$", re.I)


def find_ids(text: str) -> list[str]:
    out = []
    for m in ID_RE.finditer(text or ""):
        tok = m.group(1)
        if tok.isdigit() or sum(ch.isdigit() for ch in tok) < 3 or tok.startswith("000") or TIMESTAMP.match(tok):
            continue
        if len(tok) == 18 and tok[15:] != checksum(tok[:15]):
            continue
        if len(tok) == 15 and not (tok[0] in "0123456789a" or tok[:2] in ("m0", "kA", "ka")):
            continue
        out.append(tok)
    return out


def checksum(id15: str) -> str:
    """The three characters Salesforce appends to a 15 character Id: for each five characters, which are capitals."""
    return "".join(CHECKSUM[sum(1 << i for i, ch in enumerate(id15[j:j + 5]) if "A" <= ch <= "Z")]
                   for j in (0, 5, 10))


def id_kind(token: str) -> str:
    """'User Id (prefix 005)' without the rest of the Id."""
    kind = KEY_PREFIXES.get(token[:3])
    return f"{kind} Id (prefix {token[:3]})" if kind else f"record Id (prefix {token[:3]})"


def own_name(comp: Component) -> str:
    """The component's own name part: the field of Obj.Field, the layout of Obj-Layout."""
    if comp.type in ("CustomField", "ValidationRule", "RecordType", "CustomMetadata"):
        return comp.name.rsplit(".", 1)[-1]
    if comp.type == "Layout":
        return comp.name.split("-", 1)[-1]
    return comp.name


def namespace(comp: Component, project_namespace: str = "") -> str:
    """The managed-package namespace in the component's own name, or ""."""
    if comp.type in ("Profile", "Settings"):
        return ""
    part = NAME_SUFFIX.sub("", own_name(comp))
    if "__" not in part:
        return ""
    ns = part.split("__", 1)[0]
    return "" if not ns or ns.lower() == project_namespace.lower() else ns


@dataclass(eq=False)
class Finding:
    rule: str
    severity: str
    comp: Component
    message: str
    action: str
    line: int | None = None
    subject: str = ""              # stable across versions: tells an inherited problem from a new one
    file: str | None = None        # defaults to the component's file
    source: str = "torque"
    inherited: bool | None = None  # True: already in the baseline; False: new in this change; None: unknown
    docs: str = ""
    confirmed_by: list[str] = field(default_factory=list)
    match_lines: set = field(default_factory=set)   # lines the analyzer may report for the same problem

    @property
    def path(self) -> str:
        return self.file or self.comp.path


@dataclass
class Context:
    project: Project
    everything: list[Component]                   # all components read (the change and what surrounds it)
    grant_scope: list[Component]                  # permission sets and profiles that ship with the change
    fields: dict = field(default_factory=dict)    # "obj.field" (lower) -> CustomField component
    objects: dict = field(default_factory=dict)   # "obj" (lower) -> CustomObject component
    baseline_mode: bool = False
    scope_word: str = "in this change"       # "in scope" when no change scope was given
    scope_mode: str = ""                     # changed-from, baseline, only, files, all ("" outside check metadata)
    not_checked: list[str] = field(default_factory=list)
    said: set = field(default_factory=set)        # report-wide notes already given


def version(text: str) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())
