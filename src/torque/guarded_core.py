"""Guarded reads: the pure rules. No org call, no file, no clock.

A lane command hands this module what the org's describe says, the consultant's
policy and what the caller typed, and gets back either a query Torque built or
text safe to print. Nothing here accepts SOQL from the caller: a query is put
together from names looked up in describe and literals parsed by type. Default
deny: a field's values are used only when the consultant released that field
and the org's own facts do not forbid it; types and names are only ever a
reason to refuse. Anything unrecognized raises GuardedError, whose message
holds what the caller typed and names from describe, never a value read from a
record.

See docs/guarded-reads.md."""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from datetime import date, datetime, timezone
import re

LANES = ("counts", "config_records", "test_records")
LEVELS = ("exact", "coarse")
POLICY_SCHEMA = "torque.guarded_policy/1"


class Mask(str):
    """Text the rules print in place of a value. A stored value that happens to read the
    same (a text field that holds `<set>`) is a plain string, so the record of what a
    session was shown can tell the two apart."""


MASKED_SET, MASKED_BLANK, OTHER, UNREADABLE = Mask("<set>"), Mask("<blank>"), Mask("<other>"), Mask("<unreadable>")
ALL_FIELDS = "*"   # an object release that covers every field: custom settings and custom metadata types only

MAX_DIMENSIONS = 3
MAX_FILTERS = 6
MAX_IN_VALUES = 20
MAX_FIELDS = 60          # fields a caller or an object release names
MAX_ALL_FIELDS = 800     # every field of an object, for --all
MAX_FILL_FIELDS = 10
MAX_GROUPS = 200
CONFIG_LIMIT_DEFAULT, CONFIG_LIMIT_MAX = 200, 200
CHILD_LIMIT_DEFAULT, CHILD_LIMIT_MAX = 50, 200
MIN_CELL_DEFAULT, MIN_CELL_FLOOR = 5, 3
MAX_TYPED = 400

_IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,119}\Z")
_ID = re.compile(r"[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?\Z")
_NUMBER = re.compile(r"-?\d{1,15}(?:\.\d{1,6})?\Z")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
RECORD_TYPE_PATHS = {"recordtype.developername": "RecordType.DeveloperName", "recordtype.name": "RecordType.Name"}

DATE_TYPES = {"date", "datetime"}
NUMBER_TYPES = {"int", "long", "double", "currency", "percent"}
EXACT_TYPES = {"picklist", "multipicklist", "boolean"} | DATE_TYPES | NUMBER_TYPES
COARSE_TYPES = DATE_TYPES | NUMBER_TYPES
# Types whose values are never shown or used, in any lane, whatever the policy says.
HARD_TYPES = {"email", "phone", "address", "location", "encryptedstring", "base64"}
# The field types the rules know. A describe can name another one (a new Salesforce
# type, or a malformed answer): what a type means is then unknown, and its values are
# kept back like those of a hard type.
KNOWN_TYPES = EXACT_TYPES | HARD_TYPES | {"string", "textarea", "url", "combobox", "id", "reference", "time"}
COMPOUND_HARD = {"address", "location"}
SENSITIVE_CLASSIFICATIONS = {"confidential", "restricted", "missioncritical"}
# Whole words of an API name or label that make a release need an acknowledgement.
SENSITIVE_WORDS = frozenset((
    "birth", "birthdate", "birthday", "dob", "age", "ssn", "passport", "gender", "sex", "ethnicity", "ethnic",
    "race", "religion", "religious", "health", "medical", "diagnosis", "disability", "deceased", "death", "salary",
    "income", "immigration", "iban"))
# Objects that hold people or their messages: never released as configuration.
PEOPLE_OBJECTS = frozenset(name.casefold() for name in (
    "Account", "Contact", "Lead", "User", "Individual", "Case", "Opportunity", "Task", "Event", "EmailMessage",
    "ContentVersion", "ContentDocument", "Attachment", "Note", "FeedItem", "CampaignMember"))

_DATE_LITERAL = re.compile(
    r"(TODAY|YESTERDAY|TOMORROW"
    r"|(THIS|LAST|NEXT)_(WEEK|MONTH|QUARTER|YEAR|FISCAL_QUARTER|FISCAL_YEAR)"
    r"|(LAST|NEXT)_90_DAYS"
    r"|(LAST|NEXT)_N_(DAYS|WEEKS|MONTHS|QUARTERS|YEARS|FISCAL_QUARTERS|FISCAL_YEARS):\d{1,4}"
    r"|N_(DAYS|WEEKS|MONTHS|QUARTERS|YEARS|FISCAL_QUARTERS|FISCAL_YEARS)_AGO:\d{1,4})\Z")
# Date literals that name whole months or longer: all a coarse date filter takes.
_COARSE_LITERAL = re.compile(
    r"((THIS|LAST|NEXT)_(MONTH|QUARTER|YEAR|FISCAL_QUARTER|FISCAL_YEAR)"
    r"|(LAST|NEXT)_N_(MONTHS|QUARTERS|YEARS|FISCAL_QUARTERS|FISCAL_YEARS):\d{1,4}"
    r"|N_(MONTHS|QUARTERS|YEARS|FISCAL_QUARTERS|FISCAL_YEARS)_AGO:\d{1,4})\Z")
BUCKETS = {"year": ("CALENDAR_YEAR",), "quarter": ("CALENDAR_YEAR", "CALENDAR_QUARTER"),
           "month": ("CALENDAR_YEAR", "CALENDAR_MONTH")}


class GuardedError(Exception):
    """A refusal. Its message is safe to print: what the caller typed (shortened,
    control characters removed) and names from describe."""


def _show(text) -> str:
    text = _CONTROL.sub("?", str(text))
    return repr(text if len(text) <= 60 else text[:57] + "...")


def object_name(name) -> str:
    if not isinstance(name, str) or not _IDENT.match(name):
        raise GuardedError(f"not an object name: {_show(name)}")
    return name


# ---------------------------------------------------------------- what the org says

@dataclass(frozen=True)
class FieldInfo:
    name: str
    type: str
    label: str = ""
    groupable: bool = False
    filterable: bool = False
    aggregatable: bool = False
    calculated: bool = False
    formula: str | None = None
    name_field: bool = False
    encrypted: bool = False
    compound: str = ""                # describe compoundFieldName
    values: frozenset = frozenset()   # picklist API values, active and inactive


@dataclass(frozen=True)
class Describe:
    name: str
    queryable: bool = False
    custom_setting: bool = False
    fields: dict = dc_field(default_factory=dict)        # casefolded API name -> FieldInfo
    record_types: dict = dc_field(default_factory=dict)  # DeveloperName -> Name

    def field(self, name) -> FieldInfo:
        """The describe entry for a name the caller typed, matched without case."""
        if not isinstance(name, str) or not _IDENT.match(name.strip()):
            raise GuardedError(f"not a field name: {_show(name)}")
        found = self.fields.get(name.strip().casefold())
        if found is None:
            raise GuardedError(f"{self.name} has no field named {_show(name.strip())}")
        return found


def parse_describe(payload) -> Describe:
    """A Describe from the `result` of `sf sobject describe --json`. Anything
    missing or of the wrong shape reads as the cautious value."""
    if not isinstance(payload, dict) or not isinstance(payload.get("name"), str) \
            or not _IDENT.match(payload["name"]) or not isinstance(payload.get("fields"), list):
        raise GuardedError("the org's describe for this object could not be read")
    fields = {}
    for item in payload["fields"]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not _IDENT.match(item["name"]):
            continue
        values = frozenset(v["value"] for v in item.get("picklistValues") or []
                           if isinstance(v, dict) and isinstance(v.get("value"), str))
        formula = item.get("calculatedFormula")
        compound = item.get("compoundFieldName")
        # The facts the rules protect by: the type (a plain word), whether the field is
        # calculated and from what, whether it is encrypted, which compound field it is a
        # part of (none, or a field's name), its label (text; the sensitive words are
        # looked for in it) and whether it is the record's name. Salesforce states all of
        # them for every field. A describe that does not is not used at all: a guess (an
        # unknown type, "not calculated", "part of no address", "no label", "not the
        # name") would let a value through.
        if not isinstance(item.get("type"), str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,39}", item["type"]) \
                or not isinstance(item.get("calculated"), bool) or not isinstance(item.get("encrypted"), bool) \
                or not (formula is None or isinstance(formula, str)) \
                or (isinstance(formula, str) and formula.strip() and not item["calculated"]) \
                or "compoundFieldName" not in item \
                or not (compound is None or (isinstance(compound, str) and _IDENT.match(compound))) \
                or not isinstance(item.get("label"), str) or not isinstance(item.get("nameField"), bool) \
                or item["name"].casefold() in fields:           # a field listed twice: which one is true?
            raise GuardedError("the org's describe for this object could not be read")
        fields[item["name"].casefold()] = FieldInfo(
            name=item["name"], type=item["type"].casefold(),
            label=item["label"], groupable=item.get("groupable") is True,
            filterable=item.get("filterable") is True, aggregatable=item.get("aggregatable") is True,
            calculated=item.get("calculated") is True,
            formula=formula if isinstance(formula, str) and formula.strip() else None,
            name_field=item.get("nameField") is True, encrypted=item.get("encrypted") is not False,
            compound=compound if isinstance(compound, str) else "", values=values)
    for info in fields.values():
        # A part of a compound field is protected by what its parent is (an address, a
        # name). Salesforce lists the parent in the same describe; one that names a
        # parent it does not list cannot be read for that.
        if info.compound and info.compound.casefold() not in fields:
            raise GuardedError("the org's describe for this object could not be read")
    record_types = {}
    for info in payload.get("recordTypeInfos") or []:
        if isinstance(info, dict) and isinstance(info.get("developerName"), str) \
                and isinstance(info.get("name"), str) and info.get("master") is not True:
            record_types[info["developerName"]] = info["name"]
    return Describe(payload["name"], payload.get("queryable") is True, payload.get("customSetting") is True,
                    fields, record_types)


@dataclass(frozen=True)
class OrgFacts:
    """The org's own classification of an object's fields (FieldDefinition).
    `readable` is False when the read failed, which is not the same as an org
    that classifies nothing."""
    readable: bool = False
    fields: dict = dc_field(default_factory=dict)   # casefolded name -> (compliance, security, data type)
    # The classification is required (the policy's default): a field the org's answer
    # has no row for is then kept back, like one it classifies.
    strict: bool = False

    def classified(self, name: str, compound: str = "") -> str:
        """Why the org's classification keeps this field back, or ''. The org lists no
        row for a part of a compound field (a city, a first name, a fiscal year): its
        classification is the compound's."""
        rows = [self.fields[n.casefold()] for n in (name, compound) if n and n.casefold() in self.fields]
        for compliance, security, _ in rows:
            if compliance:
                return "the org classifies it for compliance"
            if re.sub(r"[^a-z]", "", security.casefold()) in SENSITIVE_CLASSIFICATIONS:
                return "the org classifies it as " + re.sub(r"[^A-Za-z ]", "", security)[:20]
        if self.strict and not rows:
            return "the org's classification list has no row for it"
        return ""

    def roll_up(self, name: str) -> bool:
        return self.fields.get(name.casefold(), ("", "", ""))[2].casefold().startswith("roll-up summary")


def parse_facts(records) -> OrgFacts:
    """OrgFacts from the records of the FieldDefinition query."""
    if not isinstance(records, list):
        return OrgFacts(False)
    fields = {}
    keys = ("ComplianceGroup", "SecurityClassification", "DataType")
    for record in records:
        # Every value is text or empty. Anything else (a missing key, a list, a number, a
        # field with two rows) is an answer this code does not understand, and reads as
        # "could not be read".
        if not isinstance(record, dict) or not isinstance(record.get("QualifiedApiName"), str) \
                or any(key not in record or not (record[key] is None or isinstance(record[key], str)) for key in keys) \
                or record["QualifiedApiName"].casefold() in fields:
            return OrgFacts(False)
        fields[record["QualifiedApiName"].casefold()] = tuple(record[key] or "" for key in keys)
    return OrgFacts(True, fields)


# ---------------------------------------------------------------- the consultant's policy

@dataclass(frozen=True)
class Policy:
    fields: dict = dc_field(default_factory=dict)   # "object.field" casefolded -> exact | coarse
    objects: dict = dc_field(default_factory=dict)  # casefolded object -> frozenset of casefolded fields, or {"*"}
    min_cell: int = MIN_CELL_DEFAULT
    classification: str = "required"

    def level(self, describe: Describe, info: FieldInfo) -> str | None:
        return self.fields.get(f"{describe.name}.{info.name}".casefold())


def parse_policy(payload) -> Policy:
    """The policy from its file's JSON, or the empty policy when there is no file
    (payload None). Anything malformed is refused whole: a policy that cannot be
    read releases nothing and stops the lanes."""
    if payload is None:
        return Policy()
    bad = GuardedError("the guarded policy file is malformed; the consultant fixes it with `torque guarded policy`")
    if not isinstance(payload, dict) or payload.get("schema") != POLICY_SCHEMA:
        raise bad
    fields = payload.get("fields", {})
    objects = payload.get("objects", {})
    min_cell = payload.get("counts_min_cell", MIN_CELL_DEFAULT)
    classification = payload.get("classification", "required")
    if not isinstance(fields, dict) or not isinstance(objects, dict) or type(min_cell) is not int \
            or min_cell < MIN_CELL_FLOOR or classification not in ("required", "ignore"):
        raise bad
    out = {}
    for key, level in fields.items():
        parts = key.split(".") if isinstance(key, str) else []
        if len(parts) != 2 or not all(_IDENT.match(p) for p in parts) or level not in LEVELS:
            raise bad
        out[key.casefold()] = level
    released_objects = {}
    for name, names in objects.items():
        if not isinstance(name, str) or not _IDENT.match(name) or not isinstance(names, list) or not names \
                or len(names) > MAX_FIELDS or not all(isinstance(n, str) for n in names):
            raise bad
        if names != [ALL_FIELDS] and not all(_IDENT.match(n) for n in names):
            raise bad
        released_objects[name.casefold()] = frozenset(n.casefold() for n in names)
    return Policy(out, released_objects, min_cell, classification)


def require_facts(policy: Policy, facts: OrgFacts) -> OrgFacts:
    """The facts to work with under this policy. When the classification is required,
    an answer that could not be read, or that lists no field at all, stops the lane,
    and a field the answer has no row for is kept back."""
    if policy.classification != "required":
        return OrgFacts(facts.readable, facts.fields, strict=False)
    if not facts.readable or not facts.fields:
        raise GuardedError("the org's field classification could not be read, so nothing is shown. The consultant "
                           "can retry, or accept that with `torque guarded policy set-classification --value ignore`")
    return OrgFacts(True, facts.fields, strict=True)


def _words(text: str) -> set:
    """The words of a name or a label. A word ends at anything that is not a letter or a
    digit, where a capital follows a small letter or a digit (`BirthDate`), between
    letters and digits (`DOB2`), and where an acronym is followed by a capitalised word
    (`SSNStatus` is SSN and Status). The last is read both ways, split and not, since
    `SSNs` is one word."""
    found = set()
    for spaced in (text, re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)):
        spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", spaced)
        spaced = re.sub(r"(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])", " ", spaced)
        found.update(w for w in re.split(r"[^A-Za-z0-9]+", spaced.casefold()) if w)
    return found


def sensitive_word(info: FieldInfo) -> str:
    """A sensitive word that is a whole word of the field's API name or label, in the
    singular or as a plain plural (`Salaries`, `Ages`, `Diagnoses`), or ''."""
    words = _words(info.name) | _words(info.label)
    words |= {w[:-1] for w in words if w.endswith("s")} | {w[:-2] for w in words if w.endswith("es")} \
        | {w[:-3] + "y" for w in words if w.endswith("ies")} | {w[:-2] + "is" for w in words if w.endswith("ses")}
    found = sorted(words & SENSITIVE_WORDS)
    return found[0] if found else ""


def _address_part(describe: Describe, info: FieldInfo) -> bool:
    parent = describe.fields.get(info.compound.casefold()) if info.compound else None
    return parent is not None and parent is not info and parent.type in COMPOUND_HARD


def hard_reason(describe: Describe, info: FieldInfo, facts: OrgFacts) -> str:
    """Why this field's values are never shown, even inside a released object, or ''."""
    if info.type in HARD_TYPES:
        return f"its type is {info.type}"
    if info.type not in KNOWN_TYPES:
        return "its type is one these rules do not know"
    if info.encrypted:
        return "it is encrypted"
    if _address_part(describe, info) and info.type != "picklist":
        return "it is part of an address"
    return facts.classified(info.name, info.compound)


_FORMULA_STRING = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'")
_FORMULA_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_CROSS_OBJECT = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*\s*\.\s*[A-Za-z_]|\$|\b(VLOOKUP|PARENTGROUPVAL|PREVGROUPVAL|GETSESSIONID)\b", re.IGNORECASE)
# A name in a formula, and whether a parenthesis follows it (then it is a function).
_FORMULA_NAME = re.compile(r"(?<![A-Za-z0-9_.$])([A-Za-z][A-Za-z0-9_]*)(\s*\()?")
_FORMULA_WORDS = frozenset(("true", "false", "null"))
FORMULA_DEPTH = 5


def _formula_text(info: FieldInfo) -> str:
    """The formula without its string literals and its comments. Read in one pass, so
    that a comment mark inside a string (`"/*" & Account.Name & "*/"`) or a quote
    inside a comment is not taken for the other. A string or a comment that does not
    end leaves a `$` behind, which every reader of this text takes as "cannot be
    followed"."""
    text, out, i = info.formula or "", [], 0
    while i < len(text):
        c = text[i]
        if c in "\"'":
            end = i + 1
            while end < len(text) and text[end] != c:
                end += 2 if text[end] == "\\" else 1
            out.append('""' if end < len(text) else '"" $')
            i = end + 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            out.append(" " if end >= 0 else " $")
            i = len(text) if end < 0 else end + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def formula_refs(describe: Describe, info: FieldInfo) -> tuple[list, list]:
    """(the fields of this object a formula names, the names in it that are no field
    describe shows). The second list matters: describe leaves out a field this login
    cannot see, and a formula over such a field still carries its value."""
    fields, unseen = [], []
    for name, call in _FORMULA_NAME.findall(_formula_text(info)):
        if call or name.casefold() in _FORMULA_WORDS:
            continue
        other = describe.fields.get(name.casefold())
        if other is None:
            if name not in unseen:
                unseen.append(name)
        elif other is not info and other not in fields:
            fields.append(other)
    return fields, unseen


def formula_reaches_elsewhere(info: FieldInfo, facts: OrgFacts) -> bool:
    """A calculated field whose value can come from another record: its formula
    names a relationship, a global or the session, or the org gave no formula text
    and it is not a roll-up summary."""
    if not info.calculated:
        return False
    if info.formula is None:
        return not facts.roll_up(info.name)
    return bool(_CROSS_OBJECT.search(_formula_text(info)))


def self_contained(describe: Describe, info: FieldInfo, _depth: int = 0) -> bool:
    """The field's value comes from this record alone: it is not calculated, or it is
    a formula whose text the org gave, that names no other record, no global, no
    lookup and nothing describe does not show, and whose own fields are
    self-contained too. A roll-up summary is not (its value comes from child
    records), nor is a formula without text."""
    if not info.calculated:
        return True
    if info.formula is None or _depth > FORMULA_DEPTH or _CROSS_OBJECT.search(_formula_text(info)):
        return False
    fields, unseen = formula_refs(describe, info)
    # A field of a type the rules do not know is no better a source than another record.
    return not unseen and all(other.type not in ("reference", "id") and other.type in KNOWN_TYPES
                              and self_contained(describe, other, _depth + 1) for other in fields)


def person_account_field(describe: Describe, info: FieldInfo) -> bool:
    """On Account in an org with person accounts, a field that belongs to the person:
    `X__pc` mirrors Contact's `X__c`, and the standard ones are named Person...
    Salesforce keeps the classification of such a field on Contact only, so on
    Account it would look unclassified."""
    name = info.name.casefold()
    return "ispersonaccount" in describe.fields and (name.endswith("__pc") or (
        name.startswith("person") and not name.endswith("__c")))


def blocked_reason(describe: Describe, info: FieldInfo, facts: OrgFacts, _depth: int = 0) -> str:
    """Why this field can never be released, or ''. A release is checked with this
    when it is made and again on every read."""
    hard = hard_reason(describe, info, facts)
    if hard:
        return hard
    if info.name_field:
        return "it is the record's name"
    if info.compound and info.compound.casefold() == "name" and info.name.casefold() != "name":
        return "it is part of a name"
    if person_account_field(describe, info):
        return "it is a person-account field, whose classification the org keeps on Contact; use the Contact field"
    if info.name.casefold() != "recordtypeid" and info.type not in EXACT_TYPES:
        return f"a {info.type} field cannot be released in this version"
    if info.calculated:
        if formula_reaches_elsewhere(info, facts):
            return "its formula reads another record, or the org did not give its formula"
        if info.formula is not None:
            fields, unseen = formula_refs(describe, info)
            if unseen:
                return f"its formula names {unseen[0][:60]}, which is not a field this login can see"
            for other in fields:
                if other.calculated or _depth > 0:
                    return f"its formula uses {other.name}, another calculated field"
                if blocked_reason(describe, other, facts, _depth + 1):
                    return f"its formula uses {other.name}, which cannot be released"
    return ""


def formula_sensitivity(describe: Describe, info: FieldInfo, facts: OrgFacts | None, _depth: int = 0) -> str:
    """Why a formula's value counts as sensitive, following what it is made from as
    far as it goes: a field with a sensitive word, part of an address, a field the
    org classifies (when `facts` is given), or something that cannot be followed
    (another record, a hidden field, no text, too deep). '' when none."""
    if not info.calculated:
        return ""
    if info.formula is None:
        return "" if facts is not None and facts.roll_up(info.name) else "the org did not give its formula"
    if _depth > FORMULA_DEPTH:
        return "its formula is too deep to follow"
    if _CROSS_OBJECT.search(_formula_text(info)):
        return "its formula reads another record"
    fields, unseen = formula_refs(describe, info)
    if unseen:
        return f"its formula names {unseen[0][:60]}, which is not a field this login can see"
    for other in fields:
        if other.type not in KNOWN_TYPES:
            return f"its formula uses {other.name}, whose type these rules do not know"
        if sensitive_word(other) or _address_part(describe, other) or person_account_field(describe, other) \
                or (facts is not None and facts.classified(other.name, other.compound)):
            return f"its formula uses {other.name}"
        deeper = formula_sensitivity(describe, other, facts, _depth + 1)
        if deeper:
            return f"its formula uses {other.name}"
    return ""


def acknowledgement_reason(describe: Describe, info: FieldInfo) -> str:
    """Why releasing this field needs --acknowledge-sensitive, or ''. (What cannot be
    followed in a formula is a reason to refuse the release, in blocked_reason.)"""
    word = sensitive_word(info)
    if word:
        return f"its name or label has the word {word!r}"
    if _address_part(describe, info):
        return "it is part of an address"
    if info.calculated and info.formula is not None:
        # A neutral name on a formula over a sensitive field: Years__c = ... Birthdate__c ...
        return formula_sensitivity(describe, info, None)
    return ""


def check_release(describe: Describe, info: FieldInfo, level: str, facts: OrgFacts, acknowledged: bool) -> None:
    """Refuse a release the rules do not allow. Called by `policy release`."""
    if level not in LEVELS:
        raise GuardedError("release a field as exact or coarse")
    reason = blocked_reason(describe, info, facts)
    if reason:
        raise GuardedError(f"{describe.name}.{info.name} cannot be released: {reason}")
    if level == "coarse" and info.type not in COARSE_TYPES:
        raise GuardedError(f"{describe.name}.{info.name} ({info.type}) is released as exact or not at all; coarse "
                           "is for dates and numbers")
    need = acknowledgement_reason(describe, info)
    if need and not acknowledged:
        raise GuardedError(f"{describe.name}.{info.name}: {need}. If its values may be shown for this client, "
                           "release it again with --acknowledge-sensitive")


def released(describe: Describe, info: FieldInfo, policy: Policy, facts: OrgFacts) -> str | None:
    """`exact`, `coarse` or None: how far this field is released right now. A
    release the org's facts no longer allow counts as none."""
    level = policy.level(describe, info)
    if level is None or blocked_reason(describe, info, facts):
        return None
    if level == "coarse" and info.type not in COARSE_TYPES:
        return None
    return level


def _need_release(describe: Describe, info: FieldInfo, policy: Policy, facts: OrgFacts, use: str) -> str:
    level = released(describe, info, policy, facts)
    if level is None:
        reason = blocked_reason(describe, info, facts)
        raise GuardedError(f"{info.name} is not released for this client, so it cannot be used to {use}"
                           + (f" (and cannot be released: {reason})" if reason else
                              ". The consultant releases a field with `torque guarded policy release`"))
    return level


def holds_people(describe: Describe) -> bool:
    return describe.name.casefold() in PEOPLE_OBJECTS or "ispersonaccount" in describe.fields


def settings_object(describe: Describe) -> bool:
    """A custom setting or a custom metadata type: its rows are configuration by kind."""
    return describe.custom_setting or describe.name.casefold().endswith("__mdt")


def _config_mask_reason(describe: Describe, info: FieldInfo, facts: OrgFacts, _depth: int = 0) -> str:
    """Why a field shows only whether it is set, even inside a released object, or ''.
    A formula is no way around it: one made from such a field, through any number of
    formulas, shows no more than the field does."""
    hard = hard_reason(describe, info, facts)
    if hard:
        return hard
    if info.type in ("reference", "id"):
        return "it points at another record"
    if _address_part(describe, info):
        return "it is part of an address"       # the state and country codes too: no row shows an address
    if not self_contained(describe, info):
        return "its value is calculated from other records, or the org did not give its formula"
    if info.calculated:
        for other in formula_refs(describe, info)[0]:
            if _depth >= FORMULA_DEPTH or _config_mask_reason(describe, other, facts, _depth + 1):
                return f"its formula uses {other.name}, which no row shows"
    return ""


def check_object_release(describe: Describe, names, facts: OrgFacts, *, records: bool = False,
                         sensitive: bool = False) -> list[str]:
    """Refuse an object release the rules do not allow, and give the field names to
    store. `names` is the fields whose values may be shown, or ["*"] (or nothing,
    for a settings object) for all of them, which only a custom setting or a custom
    metadata type can have. An object that holds people is never released; any
    other object that is not a settings object needs --acknowledge-records. Called
    by `policy release-object`."""
    if holds_people(describe):
        raise GuardedError(f"{describe.name} holds people's records and cannot be released as configuration")
    names = [n.strip() for n in (names or []) if isinstance(n, str) and n.strip()]
    if names == [ALL_FIELDS] or (not names and settings_object(describe)):
        if not settings_object(describe):
            raise GuardedError(f"{describe.name} is not a custom setting or a custom metadata type: name the "
                               "fields whose values may be shown, with --fields")
        return [ALL_FIELDS]
    if not names or len(names) > MAX_FIELDS:
        raise GuardedError(f"name 1 to {MAX_FIELDS} fields of {describe.name} whose values may be shown, with "
                           "--fields")
    if not settings_object(describe) and not records:
        raise GuardedError(f"{describe.name} is not a custom setting. If its rows are configuration and not "
                           "people's records, release it again with --acknowledge-records")
    out = []
    for name in names:
        info = describe.field(name)
        reason = _config_mask_reason(describe, info, facts)
        if reason:
            raise GuardedError(f"{describe.name}.{info.name} cannot be shown: {reason}. Leave it out; it will "
                               "show only whether it is set")
        need = acknowledgement_reason(describe, info)
        if need and not sensitive:
            raise GuardedError(f"{describe.name}.{info.name}: {need}. If its values may be shown for this client, "
                               "release again with --acknowledge-sensitive")
        if info.name not in out:
            out.append(info.name)
    return out


def config_shown(describe: Describe, info: FieldInfo, policy: Policy, facts: OrgFacts) -> bool:
    """Whether a row of a released configuration object shows this field's value.
    Checked on every read: a release the org's facts no longer allow shows nothing.
    A release of every field leaves out the ones whose name marks them sensitive:
    those show only when the release names them."""
    names = policy.objects.get(describe.name.casefold())
    if not names or holds_people(describe) or _config_mask_reason(describe, info, facts):
        return False
    if ALL_FIELDS in names:
        return settings_object(describe) and not acknowledgement_reason(describe, info)
    return info.name.casefold() in names


def presence_reason(describe: Describe, info: FieldInfo, policy: Policy, facts: OrgFacts) -> str:
    """Why even "is it filled in" is not answered for this field (a null filter, a
    fill count), or ''. Whether a sensitive field is filled in is a fact about
    people's records, so it follows the field's release; so does a formula made
    from one, however many formulas lie between. Whether an email or an address
    is filled in is ordinary data-quality work."""
    if info.type not in KNOWN_TYPES:
        return "its type is one these rules do not know"
    if released(describe, info, policy, facts) is not None:
        return ""
    reason = facts.classified(info.name, info.compound)
    if reason:
        return reason
    word = sensitive_word(info)
    if word:
        return f"its name or label has the word {word!r}"
    if person_account_field(describe, info):
        return "it is a person-account field, whose classification the org keeps on Contact; use the Contact field"
    return formula_sensitivity(describe, info, facts)


# ---------------------------------------------------------------- literals

def soql_string(value: str) -> str:
    if _CONTROL.search(value):
        raise GuardedError("a value holds a control character")
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _suffix(id15: str) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
    return "".join(alphabet[sum(1 << i for i, c in enumerate(id15[start:start + 5]) if "A" <= c <= "Z")]
                   for start in (0, 5, 10))


def record_id(value) -> str:
    """The 18-character form of a 15- or 18-character record id."""
    if not isinstance(value, str) or not _ID.match(value):
        raise GuardedError(f"not a record id: {_show(value)}")
    if len(value) == 18:
        if _suffix(value[:15]) != value[15:].upper():
            raise GuardedError(f"not a record id (its last three characters do not fit the rest): {_show(value)}")
        return value[:15] + value[15:].upper()
    return value + _suffix(value)


def coarse_number(text: str) -> bool:
    """At most two significant digits: 75000, 1200, 0.5, 0; not 75043 or 12.5."""
    digits = text.lstrip("-").replace(".", "").lstrip("0").rstrip("0") if "." in text \
        else text.lstrip("-").lstrip("0").rstrip("0")
    return len(digits) <= 2


def _date_value(text: str, datetime_field: bool) -> tuple[str, bool, bool]:
    """(the SOQL literal, it is a named literal, it is month-aligned or names whole months)."""
    upper = text.upper()
    if _DATE_LITERAL.match(upper):
        return upper, True, bool(_COARSE_LITERAL.match(upper))
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            day = date.fromisoformat(text)
            return day.isoformat() + ("T00:00:00Z" if datetime_field else ""), False, day.day == 1
        if datetime_field and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:\d{2})", text):
            moment = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
            moment = moment.astimezone(timezone.utc)
            aligned = (moment.day, moment.hour, moment.minute, moment.second) == (1, 0, 0, 0)
            return moment.strftime("%Y-%m-%dT%H:%M:%SZ"), False, aligned
    except ValueError:
        pass
    raise GuardedError(f"not a date for this field: {_show(text)} (use YYYY-MM-DD"
                       + (", YYYY-MM-DDTHH:MM:SSZ" if datetime_field else "") + " or a date literal such as "
                       "THIS_YEAR)")


def _split_values(text: str) -> list[str]:
    """The values of an IN list: comma separated, each optionally in single or
    double quotes so that it may hold a comma. One pair of parentheses is dropped."""
    text = text.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    values, current, quote, closed = [], [], "", False
    for char in text:
        if quote:
            if char == quote:
                quote, closed = "", True
            else:
                current.append(char)
        elif char == ",":
            values.append("".join(current) if closed else "".join(current).strip())
            current, closed = [], False
        elif closed:
            if char.strip():
                raise GuardedError(f"could not read the list of values: {_show(text)}")
        elif char in "'\"" and not "".join(current).strip():
            quote, current = char, []
        else:
            current.append(char)
    if quote:
        raise GuardedError(f"a quote is not closed in: {_show(text)}")
    values.append("".join(current) if closed else "".join(current).strip())
    if any(v == "" for v in values):
        raise GuardedError(f"an empty value in: {_show(text)}")
    return values


def _unquote(text: str) -> tuple[str, bool]:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1], True
    return text, False


def _member(value: str, allowed, where: str) -> str:
    for candidate in allowed:
        if candidate.casefold() == value.casefold():
            return candidate
    raise GuardedError(f"{_show(value)} is not a configured value of {where} in this org")


# ---------------------------------------------------------------- filters

@dataclass(frozen=True)
class Filter:
    field: str        # the API name in describe's spelling, or a RecordType path
    op: str           # =, !=, <, <=, >, >=, IN, NOT IN
    literals: tuple   # SOQL literals, already quoted where needed
    typed: str        # what the caller typed, for the record of the call

    def soql(self) -> str:
        if self.op in ("IN", "NOT IN"):
            return f"{self.field} {self.op} ({', '.join(self.literals)})"
        return f"{self.field} {self.op} {self.literals[0]}"


_FILTER = re.compile(r"\s*([A-Za-z][A-Za-z0-9_.]*)\s*(?:(!=|<=|>=|=|<|>)|\s(NOT\s+IN|IN)(?=[\s(]))\s*(.*?)\s*\Z",
                     re.IGNORECASE | re.DOTALL)


def _record_type(describe: Describe, policy: Policy, facts: OrgFacts, use: str) -> None:
    info = describe.fields.get("recordtypeid")
    if info is None or not describe.record_types:
        raise GuardedError(f"{describe.name} has no record types")
    _need_release(describe, info, policy, facts, use)


def parse_filter(text, describe: Describe, policy: Policy, facts: OrgFacts) -> Filter:
    """One `Field OP Value` filter. A value is accepted only for a released field,
    and only as fine as its release allows; `= null` and `!= null` are accepted
    for a field describe says can be filtered, unless the org classifies it or
    its name marks it sensitive and it is not released."""
    if not isinstance(text, str) or len(text) > MAX_TYPED or _CONTROL.search(text):
        raise GuardedError(f"a filter is longer than {MAX_TYPED} characters or holds a control character")
    match = _FILTER.match(text)
    if not match:
        raise GuardedError(f"could not read the filter {_show(text)}: write Field OP Value, with OP one of "
                           "=, !=, <, <=, >, >=, IN, NOT IN")
    name, symbol, word, raw = match.group(1), match.group(2), match.group(3), match.group(4)
    op = symbol or " ".join(word.upper().split())
    typed = text.strip()        # as typed: a value's own spaces are part of the value
    if raw == "":
        raise GuardedError(f"the filter {_show(text)} has no value")
    path = RECORD_TYPE_PATHS.get(name.casefold())
    if path is None and "." in name:
        raise GuardedError(f"{_show(name)}: the only relationship paths are RecordType.DeveloperName and "
                           "RecordType.Name")
    bare, quoted = _unquote(raw)
    if bare.casefold() == "null" and not quoted:
        if op not in ("=", "!="):
            raise GuardedError("null is compared with = or != only")
        if path:
            _record_type(describe, policy, facts, "filter")
            return Filter("RecordTypeId", op, ("null",), typed)
        info = describe.field(name)
        if not info.filterable:
            raise GuardedError(f"{info.name} cannot be filtered in this org")
        reason = presence_reason(describe, info, policy, facts)
        if reason:
            raise GuardedError(f"{info.name} is not released for this client and {reason}, so it cannot be "
                               "tested for null")
        return Filter(info.name, op, ("null",), typed)
    values = _split_values(raw) if op in ("IN", "NOT IN") else [bare]
    if len(values) > MAX_IN_VALUES:
        raise GuardedError(f"at most {MAX_IN_VALUES} values in one list")
    if path:
        _record_type(describe, policy, facts, "filter")
        allowed = set(describe.record_types) if path.endswith("DeveloperName") else set(describe.record_types.values())
        if op not in ("=", "!=", "IN", "NOT IN"):
            raise GuardedError("a record type is compared with =, !=, IN or NOT IN")
        return Filter(path, op, tuple(soql_string(_member(v, allowed, path)) for v in values), typed)
    info = describe.field(name)
    if info.name.casefold() == "recordtypeid":
        raise GuardedError("filter on RecordType.DeveloperName or RecordType.Name, not on RecordTypeId")
    if not info.filterable:
        raise GuardedError(f"{info.name} cannot be filtered in this org")
    level = _need_release(describe, info, policy, facts, "filter by value (only = null and != null work without "
                          "a release)")
    if info.type == "boolean":
        if op not in ("=", "!=") or values[0].casefold() not in ("true", "false"):
            raise GuardedError(f"a checkbox is compared with = or != and true or false: {_show(typed)}")
        return Filter(info.name, op, (values[0].casefold(),), typed)
    if info.type == "picklist":
        if op not in ("=", "!=", "IN", "NOT IN"):
            raise GuardedError("a picklist is compared with =, !=, IN or NOT IN")
        return Filter(info.name, op, tuple(soql_string(_member(v, info.values, info.name)) for v in values), typed)
    if info.type == "multipicklist":
        raise GuardedError(f"{info.name} is a multi-select picklist: it can be tested for null only")
    if op in ("IN", "NOT IN"):
        raise GuardedError("a date or a number is compared with =, !=, <, <=, > or >=")
    if info.type in DATE_TYPES:
        literal, named, whole = _date_value(values[0], info.type == "datetime")
        if level == "coarse" and (not whole or (not named and op not in ("<", ">="))):
            raise GuardedError(f"{info.name} is released as coarse: filter it with >= or < the first day of a "
                               "month, or with a literal such as THIS_YEAR or LAST_N_MONTHS:6")
        return Filter(info.name, op, (literal,), typed)
    if not _NUMBER.match(values[0]):
        raise GuardedError(f"not a number: {_show(values[0])}")
    if level == "coarse" and not coarse_number(values[0]):
        raise GuardedError(f"{info.name} is released as coarse: filter it with a number of at most two "
                           "significant digits, such as 5000 or 0.5")
    return Filter(info.name, op, (values[0],), typed)


def parse_filters(texts, describe: Describe, policy: Policy, facts: OrgFacts) -> list[Filter]:
    texts = list(texts or [])
    if len(texts) > MAX_FILTERS:
        raise GuardedError(f"at most {MAX_FILTERS} filters")
    return [parse_filter(t, describe, policy, facts) for t in texts]


def _where(filters: list[Filter]) -> str:
    return " WHERE " + " AND ".join(f.soql() for f in filters) if filters else ""


# ---------------------------------------------------------------- counts

@dataclass(frozen=True)
class Dimension:
    label: str           # the column name the caller sees
    expressions: tuple   # SOQL expressions, each selected under its own alias
    kind: str            # picklist, boolean, record_type, year, quarter, month
    values: frozenset = frozenset()


def parse_dimension(text, describe: Describe, policy: Policy, facts: OrgFacts) -> Dimension:
    if not isinstance(text, str) or len(text) > 200 or _CONTROL.search(text):
        raise GuardedError("a group-by name is too long or holds a control character")
    text = text.strip()
    path = RECORD_TYPE_PATHS.get(text.casefold())
    if path:
        _record_type(describe, policy, facts, "group by")
        names = frozenset(describe.record_types) if path.endswith("DeveloperName") \
            else frozenset(describe.record_types.values())
        return Dimension(path, (path,), "record_type", names)
    name, _, bucket = text.partition(":")
    if "." in name:
        raise GuardedError(f"{_show(name)}: the only relationship paths are RecordType.DeveloperName and "
                           "RecordType.Name")
    info = describe.field(name)
    level = _need_release(describe, info, policy, facts, "group by")
    bucket = bucket.strip().casefold()
    if info.type in DATE_TYPES:
        if bucket not in BUCKETS:
            raise GuardedError(f"group a date by year, quarter or month, for example {info.name}:month")
        return Dimension(f"{info.name}:{bucket}", tuple(f"{fn}({info.name})" for fn in BUCKETS[bucket]), bucket)
    if bucket:
        raise GuardedError(f"{info.name} is not a date; :year, :quarter and :month apply to dates only")
    if info.type not in ("picklist", "boolean") or level != "exact":
        raise GuardedError(f"{info.name} ({info.type}) cannot be grouped by; group by a picklist or a checkbox "
                           "released as exact, a record type, or a date by year, quarter or month")
    if not info.groupable:
        raise GuardedError(f"{info.name} cannot be grouped by in this org")
    return Dimension(info.name, (info.name,), info.type, info.values)


def build_counts_query(describe: Describe, dimensions: list[Dimension], filters: list[Filter]) -> tuple[str, list]:
    """(the SOQL, the aliases of each dimension). Every selected expression has a
    fixed alias, so a result key never depends on a field's name. A grouped query
    asks for one group more than may be shown, to tell "too many" from "all"."""
    if not describe.queryable:
        raise GuardedError(f"{describe.name} cannot be queried")
    if len(dimensions) > MAX_DIMENSIONS:
        raise GuardedError(f"at most {MAX_DIMENSIONS} group-by fields")
    if len({d.label.casefold() for d in dimensions}) != len(dimensions):
        raise GuardedError("a group-by field is named twice")
    selected, grouped, aliases = [], [], []
    for dimension in dimensions:
        mine = []
        for expression in dimension.expressions:
            alias = f"d{len(selected)}"
            selected.append(f"{expression} {alias}")
            grouped.append(expression)
            mine.append(alias)
        aliases.append(mine)
    soql = f"SELECT {', '.join(selected + ['COUNT(Id) n'])} FROM {object_name(describe.name)}{_where(filters)}"
    if grouped:
        soql += f" GROUP BY {', '.join(grouped)} LIMIT {MAX_GROUPS + 1}"
    return soql, aliases


def dimension_value(dimension: Dimension, raw: list) -> str:
    """What a group shows for one dimension. Never raises. A picklist or record
    type value that is not configured in the org is not printed: a picklist that
    is not restricted can hold any text a record was given."""
    try:
        if any(v is None for v in raw):
            return MASKED_BLANK
        if dimension.kind in ("picklist", "record_type"):
            return raw[0] if isinstance(raw[0], str) and raw[0] in dimension.values else OTHER
        if dimension.kind == "boolean":
            return "true" if raw[0] is True else "false" if raw[0] is False else OTHER
        if not all(type(v) is int for v in raw) or not 1 <= raw[0] <= 9999:
            return OTHER
        if dimension.kind == "year":
            return f"{raw[0]:04d}"
        if dimension.kind == "quarter" and 1 <= raw[1] <= 4:
            return f"{raw[0]:04d}-Q{raw[1]}"
        if dimension.kind == "month" and 1 <= raw[1] <= 12:
            return f"{raw[0]:04d}-{raw[1]:02d}"
        return OTHER
    except Exception:
        return OTHER


def _min_cell(policy: Policy) -> int:
    if type(policy.min_cell) is not int or policy.min_cell < MIN_CELL_FLOOR:
        raise GuardedError("the guarded policy's counts_min_cell is not a whole number of "
                           f"{MIN_CELL_FLOOR} or more")
    return policy.min_cell


def shape_counts(dimensions: list[Dimension], aliases: list, records, policy: Policy) -> dict:
    """The printable result of a counts query. A group smaller than the minimum
    cell is left out, its values with it, and the result does not say whether any
    were: an empty result and a small one look the same."""
    minimum = _min_cell(policy)
    bad = GuardedError("the org's answer was not in the expected form; nothing is shown")
    if not isinstance(records, list):
        raise bad
    if len(records) > MAX_GROUPS:
        raise GuardedError(f"more than {MAX_GROUPS} groups; group by fewer or coarser fields")
    cells: dict = {}
    for record in records:
        if not isinstance(record, dict) or type(record.get("n")) is not int or record["n"] < 0:
            raise bad
        if any(alias not in record for mine in aliases for alias in mine):
            raise bad
        key = tuple(dimension_value(d, [record[a] for a in mine]) for d, mine in zip(dimensions, aliases))
        cells[key] = cells.get(key, 0) + record["n"]
    rows = [{"values": list(key), "count": count}
            for key, count in sorted(cells.items(), key=lambda item: (-item[1], item[0])) if count >= minimum]
    return {"columns": [d.label for d in dimensions], "rows": rows, "min_cell": minimum}


def parse_fill_fields(names, describe: Describe, policy: Policy, facts: OrgFacts) -> list[FieldInfo]:
    names = [n.strip() for n in (names or []) if isinstance(n, str) and n.strip()]
    if not names or len(names) > MAX_FILL_FIELDS:
        raise GuardedError(f"name 1 to {MAX_FILL_FIELDS} fields")
    out, seen = [], set()
    for name in names:
        info = describe.field(name)
        if info.name.casefold() in seen:
            continue
        seen.add(info.name.casefold())
        if not info.aggregatable or info.type in ("address", "location", "base64"):
            raise GuardedError(f"{info.name} ({info.type}) cannot be counted in this org")
        reason = presence_reason(describe, info, policy, facts)
        if reason:
            raise GuardedError(f"{info.name} is not released for this client and {reason}, so its fill is not "
                               "counted")
        out.append(info)
    return out


def build_fill_query(describe: Describe, fields: list[FieldInfo], filters: list[Filter]) -> str:
    if not describe.queryable:
        raise GuardedError(f"{describe.name} cannot be queried")
    counted = [f"COUNT({info.name}) f{i}" for i, info in enumerate(fields)]
    return f"SELECT {', '.join(['COUNT(Id) n', *counted])} FROM {object_name(describe.name)}{_where(filters)}"


def shape_fill(fields: list[FieldInfo], records, policy: Policy) -> dict:
    """How many records have each field filled in. A count is shown only when
    both it and its complement are 0 or at least the minimum cell."""
    minimum = _min_cell(policy)
    bad = GuardedError("the org's answer was not in the expected form; nothing is shown")
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        raise bad
    record = records[0]
    total = record.get("n")
    if type(total) is not int or total < 0:
        raise bad
    if total < minimum:
        return {"total": None, "rows": [], "min_cell": minimum}
    rows = []
    for index, info in enumerate(fields):
        filled = record.get(f"f{index}")
        if type(filled) is not int or not 0 <= filled <= total:
            raise bad
        blank = total - filled
        shown = (filled == 0 or filled >= minimum) and (blank == 0 or blank >= minimum)
        rows.append({"field": info.name, "filled": filled if shown else None})
    return {"total": total, "rows": rows, "min_cell": minimum}


# ---------------------------------------------------------------- stored values

def _blank(value) -> bool:
    return value is None or value == "" or value == [] or value == {}


def presence(value) -> str:
    return MASKED_BLANK if _blank(value) else MASKED_SET


def stored(value):
    """A value as the org holds it, when it is a plain scalar; never raises."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else UNREADABLE
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()
                if k != "attributes" and (v is None or isinstance(v, (str, bool, int, float)))}
    return UNREADABLE


def select_names(names, describe: Describe, *, default_all: bool) -> list[FieldInfo]:
    """The fields a config or record command reads. `Id` comes first. Without
    names, every field that can be selected on its own (no base64, no compound
    parent), up to the limit."""
    names = [n.strip() for n in (names or []) if isinstance(n, str) and n.strip()]
    if not names:
        if not default_all:
            raise GuardedError("name the fields with --fields, or ask for all of them with --all")
        infos = [i for i in describe.fields.values() if i.type not in ("base64", "address", "location")]
        if len(infos) > MAX_ALL_FIELDS:
            raise GuardedError(f"{describe.name} has more than {MAX_ALL_FIELDS} fields; name the ones you need "
                               "with --fields")
    else:
        if len(names) > MAX_FIELDS:
            raise GuardedError(f"at most {MAX_FIELDS} fields")
        infos = [describe.field(n) for n in names]
    out, seen = [], set()
    for info in [describe.field("Id"), *infos]:
        if info.name.casefold() not in seen:
            seen.add(info.name.casefold())
            out.append(info)
    return out


def build_config_query(describe: Describe, policy: Policy, fields: list[FieldInfo], limit) -> str:
    if describe.name.casefold() not in policy.objects:
        raise GuardedError(f"{describe.name} is not released as a configuration object for this client. The "
                           "consultant releases one with `torque guarded policy release-object`")
    if holds_people(describe):
        raise GuardedError(f"{describe.name} holds people's records and is never read as configuration")
    if ALL_FIELDS in policy.objects[describe.name.casefold()] and not settings_object(describe):
        raise GuardedError(f"{describe.name} is released with all its fields, which only a custom setting or a "
                           "custom metadata type can be. The consultant releases it again with --fields")
    if not describe.queryable:
        raise GuardedError(f"{describe.name} cannot be queried")
    if type(limit) is not int or not 1 <= limit <= CONFIG_LIMIT_MAX:
        raise GuardedError(f"the limit is a whole number from 1 to {CONFIG_LIMIT_MAX}")
    return f"SELECT {', '.join(i.name for i in fields)} FROM {object_name(describe.name)} ORDER BY Id LIMIT {limit}"


def show_config_row(describe: Describe, fields: list[FieldInfo], record, policy: Policy, facts: OrgFacts) -> dict:
    """A row of a released configuration object. A field the object's release covers
    shows as stored; every other field shows only whether it is set. Never raises."""
    out = {}
    for info in fields:
        try:
            value = record.get(info.name) if isinstance(record, dict) else None
            if info.name == "Id":
                out[info.name] = stored(value) if isinstance(value, str) and _ID.match(value) else UNREADABLE
            elif config_shown(describe, info, policy, facts):
                out[info.name] = stored(value)
            else:
                out[info.name] = presence(value)
        except Exception:
            out[info.name] = UNREADABLE
    return out


def show_test_value(describe: Describe, info: FieldInfo, value, policy: Policy, facts: OrgFacts, registered):
    """One field of a registered test record. As stored, except a reference to a
    record that is not registered, and a calculated field whose value can come
    from another record (a roll-up summary, a formula that reaches a parent or
    builds on one that does), which shows only if that field is released. Never
    raises."""
    try:
        if info.type in ("reference", "id") and info.name != "Id":
            if _blank(value):
                return None
            if not isinstance(value, str) or not _ID.match(value):
                return UNREADABLE
            try:
                full = record_id(value)
            except GuardedError:
                return UNREADABLE
            return full if full in registered else Mask(f"<unregistered {full[:3]}>")
        if info.type not in KNOWN_TYPES:
            return presence(value)      # what a value of this type means is not known
        if not self_contained(describe, info) and released(describe, info, policy, facts) is None:
            return presence(value)
        return stored(value)
    except Exception:
        return UNREADABLE


def build_record_query(describe: Describe, fields: list[FieldInfo], rid: str) -> str:
    if not describe.queryable:
        raise GuardedError(f"{describe.name} cannot be queried")
    return (f"SELECT {', '.join(i.name for i in fields)} FROM {object_name(describe.name)} "
            f"WHERE Id = {soql_string(record_id(rid))} LIMIT 1")


def parse_child(text, child: Describe) -> FieldInfo:
    """The lookup field of `Child.Lookup` (the object part is checked by the caller)."""
    if not isinstance(text, str) or text.count(".") != 1:
        raise GuardedError("name the children as Object.LookupField, for example Opportunity.AccountId")
    info = child.field(text.split(".")[1])
    if info.type != "reference" or not info.filterable:
        raise GuardedError(f"{child.name}.{info.name} is not a lookup that can be filtered")
    return info


def build_child_ids_query(child: Describe, lookup: FieldInfo, parent: str, limit: int = MAX_GROUPS) -> str:
    if not child.queryable:
        raise GuardedError(f"{child.name} cannot be queried")
    return (f"SELECT Id FROM {object_name(child.name)} WHERE {lookup.name} = {soql_string(record_id(parent))} "
            f"ORDER BY Id LIMIT {int(limit) + 1}")


def build_children_query(child: Describe, fields: list[FieldInfo], ids: list[str]) -> str:
    if not ids or len(ids) > CHILD_LIMIT_MAX:
        raise GuardedError("no registered children to read")
    listed = ", ".join(soql_string(record_id(i)) for i in ids)
    return f"SELECT {', '.join(i.name for i in fields)} FROM {object_name(child.name)} WHERE Id IN ({listed})"
