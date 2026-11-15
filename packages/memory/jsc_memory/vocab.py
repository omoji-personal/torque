"""The words Torque knows: from a lesson's text, a topic or a command to tasks and
metadata types.

Small and deterministic on purpose: no model call, no index. What it infers for a new
lesson is printed when the lesson is saved, so the agent or the consultant can correct
it (`torque lesson edit ID --set applies.types=Flow,CustomField`).
"""
from __future__ import annotations

import re

# (words, tasks, metadata types). Matched whole-word, case-insensitive, in this order.
VOCABULARY = (
    (r"flows?|subflows?|flow builder|record-triggered|autolaunched|screen actions?|process builder"
     r"|FlowDefinition(?:View)?", ("flow-build",), ("Flow",)),
    (r"workflow rules?", ("flow-build",), ("Workflow",)),
    (r"field-level security|FLS|field access|field permissions?", ("field-build", "access"),
     ("CustomField", "PermissionSet")),
    (r"(?:custom|new|formula|roll-?up summary|lookup|master-detail|picklist|required|currency|checkbox|text|date"
     r"|number|percent) fields?|fields? (?:descriptions?|labels?|history|sets?|dependenc\w+)|help text|picklists?"
     r"|global value sets?", ("field-build",), ("CustomField",)),
    (r"permission set groups?", ("access",), ("PermissionSetGroup",)),
    (r"permission sets?|permsets?", ("access",), ("PermissionSet",)),
    (r"profiles?", ("access",), ("Profile",)),
    (r"sharing rules?|sharing settings|org-wide defaults?|OWD|role hierarchy|record access", ("access",),
     ("SharingRules",)),
    (r"run flows|effective access|object permissions?|log in as|login as", ("access",), ()),
    (r"validation rules?", ("validation",), ("ValidationRule",)),
    (r"compact layouts?", ("layout",), ("CompactLayout",)),
    (r"page layouts?|layouts?", ("layout",), ("Layout",)),
    (r"lightning (?:record |app |home )?pages?|flexi ?pages?|record pages?|app builder|dynamic forms?", ("layout",),
     ("FlexiPage",)),
    (r"apex triggers?", ("apex",), ("ApexTrigger",)),
    (r"apex|test class(?:es)?|batchable|queueable|future methods?", ("apex",), ("ApexClass",)),
    (r"lwc|lightning web components?", ("lwc",), ("LightningComponentBundle",)),
    (r"aura components?", ("lwc",), ("AuraDefinitionBundle",)),
    (r"deploy\w*|change sets?|package\.xml|manifests?|destructive ?changes|validate-only|dry[- ]run", ("deploy",), ()),
    (r"retriev\w+|metadata listing", ("retrieve",), ()),
    (r"soql|sosl|quer(?:y|ies)|tooling api", ("query",), ()),
    (r"data (?:update|create|delete|upsert|undelete|import|export|bulk)\b|data loads?|data loader|dataloader"
     r"|bulk api|bulk (?:update|upsert|delete|import)s?|imports?|upserts?|csv|migrat\w+|backfill\w*|mass updates?",
     ("data-load",), ()),
    (r"sandbox refresh\w*|refreshed sandbox|post-refresh|after (?:a|the) (?:sandbox )?refresh", ("sandbox-refresh",), ()),
    (r"sandbox(?:es)?", ("sandbox",), ()),
    (r"browsers?|playwright|chrome|headless|screenshots?|ui tests?|devtools", ("browser-test",), ()),
    (r"sign(?:ing|ed)?[- ]in|log(?:ging|ged)?[- ]in|logins?|front ?door|frontdoor|mfa|passkeys?|sso"
     r"|authenticat\w+|org (?:login|open)", ("sign-in",), ()),
    (r"dashboards?", ("report",), ("Dashboard",)),
    (r"list views?", ("report",), ("ListView",)),
    (r"report types?|reports? (?:folder|filter|builder|chart)s?|(?:summary|tabular|matrix|joined|custom|standard) "
     r"reports?|reports and dashboards", ("report",), ("Report",)),
    (r"named credentials?", ("integration",), ("NamedCredential",)),
    (r"connected apps?|external client apps?", ("integration",), ("ConnectedApp",)),
    (r"remote site settings?", ("integration",), ("RemoteSiteSetting",)),
    (r"integrations?|callouts?|webhooks?|connectors?|endpoints?|rest api|middleware|platform events?"
     r"|outbound messages?|api (?:user|key|limit)s?", ("integration",), ()),
    (r"roll-?ups?|roll up|dlrs|declarative lookup rollup\w*|customizable rollups?|crlp|data processing engine",
     ("rollup",), ()),
    (r"test (?:as|users?|records?|data|scripts?|plans?)|uat|qa|dummy records?|acceptance tests?|regression",
     ("testing",), ()),
    (r"production|prod", ("production",), ()),
    (r"email templates?", ("email",), ("EmailTemplate",)),
    (r"deliverability|email alerts?|bounce\w*|outbound emails?|text gateways?|sms", ("email",), ()),
    (r"record types?", ("object-build",), ("RecordType",)),
    (r"custom metadata(?: types?)?", ("object-build",), ("CustomMetadata",)),
    (r"custom objects?|custom settings?|junction objects?|data model", ("object-build",), ("CustomObject",)),
    (r"scheduled (?:jobs?|flows?|apex|paths?)|cron\w*|AsyncApexJob", ("scheduling",), ()),
    (r"managed packages?|unlocked packages?|package versions?|namespaces?|appexchange|beta versions?", ("packaging",),
     ("InstalledPackage",)),
    (r"descriptions?|document(?:ed|ation|ing)?|release notes|runbooks?|training guides?|data dictionary",
     ("documentation",), ()),
    (r"npsp|nonprofit cloud|nonprofit success pack|households?|gift entry|recurring donations?|gau allocations?"
     r"|soft credits?|campaign members?|outbound funds|accounting subledger", ("nonprofit",), ()),
    (r"approval process(?:es)?", ("approval",), ("ApprovalProcess",)),
    (r"duplicate rules?", ("data-quality",), ("DuplicateRule",)),
    (r"matching rules?", ("data-quality",), ("MatchingRules",)),
    (r"shared (?:org|sandbox)s?|other teams?", ("shared-org",), ()),
    (r"roll ?back|revert\w*|restor\w+|backups?", ("recovery",), ()),
    (r"debug logs?|fault emails?|error emails?|unhandled fault|stack traces?|troubleshoot\w*|diagnos\w+",
     ("diagnose",), ()),
)
# An underscore joins a name (Update_Layout_Flow is one name, not the word layout).
_COMPILED = tuple((re.compile(r"(?<![A-Za-z0-9_])(?:" + words + r")(?![A-Za-z0-9_])", re.I), tasks, types)
                  for words, tasks, types in VOCABULARY)

# Source-format file endings (lower case) and folders, to their metadata type.
SUFFIXES = {
    ".flow-meta.xml": "Flow", ".field-meta.xml": "CustomField", ".object-meta.xml": "CustomObject",
    ".permissionset-meta.xml": "PermissionSet", ".permissionsetgroup-meta.xml": "PermissionSetGroup",
    ".profile-meta.xml": "Profile", ".validationrule-meta.xml": "ValidationRule", ".layout-meta.xml": "Layout",
    ".compactlayout-meta.xml": "CompactLayout", ".flexipage-meta.xml": "FlexiPage", ".cls": "ApexClass",
    ".cls-meta.xml": "ApexClass", ".trigger": "ApexTrigger", ".trigger-meta.xml": "ApexTrigger",
    ".recordtype-meta.xml": "RecordType", ".report-meta.xml": "Report", ".dashboard-meta.xml": "Dashboard",
    ".md-meta.xml": "CustomMetadata", ".listview-meta.xml": "ListView", ".quickaction-meta.xml": "QuickAction",
    ".tab-meta.xml": "CustomTab", ".app-meta.xml": "CustomApplication", ".email-meta.xml": "EmailTemplate",
    ".page": "ApexPage", ".page-meta.xml": "ApexPage", ".duplicaterule-meta.xml": "DuplicateRule",
    ".matchingrule-meta.xml": "MatchingRules", ".sharingrules-meta.xml": "SharingRules",
    ".workflow-meta.xml": "Workflow", ".namedcredential-meta.xml": "NamedCredential",
    ".remotesite-meta.xml": "RemoteSiteSetting", ".connectedapp-meta.xml": "ConnectedApp",
    ".custompermission-meta.xml": "CustomPermission", ".globalvalueset-meta.xml": "GlobalValueSet",
    ".labels-meta.xml": "CustomLabels", ".approvalprocess-meta.xml": "ApprovalProcess",
    ".flowtest-meta.xml": "FlowTest", ".resource-meta.xml": "StaticResource", ".weblink-meta.xml": "WebLink",
}
FOLDERS = {
    "flows": "Flow", "fields": "CustomField", "objects": "CustomObject", "permissionsets": "PermissionSet",
    "permissionsetgroups": "PermissionSetGroup", "profiles": "Profile", "validationrules": "ValidationRule",
    "layouts": "Layout", "flexipages": "FlexiPage", "classes": "ApexClass", "triggers": "ApexTrigger",
    "recordtypes": "RecordType", "reports": "Report", "dashboards": "Dashboard", "custommetadata": "CustomMetadata",
    "lwc": "LightningComponentBundle", "aura": "AuraDefinitionBundle", "listviews": "ListView",
    "quickactions": "QuickAction", "tabs": "CustomTab", "applications": "CustomApplication",
    "email": "EmailTemplate", "pages": "ApexPage", "workflows": "Workflow", "namedcredentials": "NamedCredential",
    "sharingrules": "SharingRules", "approvalprocesses": "ApprovalProcess", "staticresources": "StaticResource",
    "labels": "CustomLabels", "duplicaterules": "DuplicateRule", "flowtests": "FlowTest",
}
TYPES = {name.lower(): name for name in
         [*SUFFIXES.values(), *FOLDERS.values(), *(t for _, _, types in VOCABULARY for t in types)]}
_KEYWORD = re.compile(r"(?<![\w.])(?:[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+(?:\.[A-Za-z_]+)?"
                      r"|[A-Za-z]\w*__(?:c|r|mdt|e|x|b))(?!\w)")
# An error code as Salesforce prints it: capitals joined by underscores.
_ERROR_CODE = re.compile(r"(?<![\w.])[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+(?!\w)")
# Error messages worth recognising in a lesson's text, as (how the text may spell it, the matcher to store).
ERROR_PHRASES = tuple((re.compile(spelling, re.I), matcher) for spelling, matcher in (
    (r"no such column", "No such column"),
    (r"didn'?t understand relationship", "Didn't understand relationship"),
    (r"sobject type .{0,80}? is not supported", "sObject type * is not supported"),
    (r"disjunctions? (?:are )?not supported", "Disjunctions not supported"),
    (r"can ?not be grouped", "can*not be grouped"),
    (r"can ?not be filtered", "can*not be filtered"),
    (r"invalid operator on id field", "invalid operator on id field"),
    (r"implementation restriction", "Implementation restriction"),
    (r"too many soql queries", "Too many SOQL queries"),
    (r"too many dml statements", "Too many DML statements"),
    (r"does not support query", "does not support query"),
    (r"data value too large", "data value too large"),
    (r"insufficient access rights on cross-reference id", "insufficient access rights on cross-reference id"),
    (r"duplicate value found", "duplicate value found"),
    (r"required fields are missing", "Required fields are missing"),
    (r"unexpected token", "Unexpected token"),
))
_STOP = {"the", "and", "for", "with", "you", "are", "about", "that", "this", "when", "from", "into", "not", "but",
         "was", "has", "have", "its", "all", "any", "can", "does", "then", "than", "how", "what", "why", "use",
         "using", "torque", "lesson", "lessons"}


def _unique(values) -> list[str]:
    return list(dict.fromkeys(values))


def infer(text: str) -> tuple[list[str], list[str]]:
    """(tasks, metadata types) the words of a text point to. A type spelled as
    Salesforce spells it (CustomField, PermissionSet) counts as that type."""
    tasks: list[str] = []
    types: list[str] = []
    for pattern, row_tasks, row_types in _COMPILED:
        if pattern.search(text or ""):
            tasks += row_tasks
            types += row_types
    types += [word for word in re.findall(r"[A-Z][a-z]+(?:[A-Z][a-z]+)+", text or "") if TYPES.get(word.lower()) == word]
    return _unique(tasks), _unique(types)


def keywords(text: str, limit: int = 8) -> list[str]:
    """Distinctive tokens of a text: API names and object.field references."""
    found = _unique(match.group() for match in _KEYWORD.finditer(text or ""))
    return [word for word in found if word.lower() not in TYPES][:limit]


def errors(text: str, limit: int = 6) -> list[str]:
    """Error matchers a text quotes: error codes in capitals with underscores, and the
    known error messages (ERROR_PHRASES)."""
    found = [match.group() for match in _ERROR_CODE.finditer(text or "")]
    found += [matcher for spelling, matcher in ERROR_PHRASES if spelling.search(text or "")]
    return _unique(found)[:limit]


def normal_error(text: str) -> str:
    """An error text as matchers see it: lower case, one space between words."""
    return " ".join(str(text or "").lower().split())


def error_matches(matcher: str, error: str) -> bool:
    """A lesson's error matcher against a normalised error text: the matcher is a piece of
    the text, in any case, and `*` in it stands for any run of characters. Nothing else
    is special: no regular expression is ever built from a lesson's text."""
    parts = [part.strip() for part in normal_error(matcher).split("*")]
    if sum(len(part) for part in parts) < 4:
        return False
    position = 0
    for part in parts:
        found = error.find(part, position)
        if found < 0:
            return False
        position = found + len(part)
    return True


def error_codes(text: str) -> list[str]:
    return _unique(match.group() for match in _ERROR_CODE.finditer(text or ""))


def error_wording(text: str) -> list[str]:
    """The words of an error message with quoted names and numbers taken out, to compare
    two messages of the same class by their wording."""
    text = re.sub(r"'[^'\s]{1,80}'|\"[^\"\s]{1,80}\"|`[^`\s]{1,80}`", " * ", str(text or ""))
    return re.findall(r"[a-z_]+|\*", text.lower())


def shares_wording(error: str, other: str, run: int = 4) -> bool:
    """Two error texts name the same error code, or share `run` consecutive words."""
    if any(code in other for code in error_codes(error)):
        return True
    first, second = error_wording(error), error_wording(other)
    grams = {tuple(second[index:index + run]) for index in range(len(second) - run + 1)}
    return any(tuple(first[index:index + run]) in grams for index in range(len(first) - run + 1))


def tag(text: str) -> str:
    """A task word as it is stored: lower case, hyphens for spaces."""
    return re.sub(r"[\s_]+", "-", str(text).strip().lower())


def canonical_type(text: str) -> str:
    """A metadata type as Salesforce spells it (flow -> Flow), or the text unchanged."""
    name = str(text).strip()
    return TYPES.get(name.lower().replace(" ", "")) or (infer(name)[1] or [name])[0]


def types_for_path(path: str) -> list[str]:
    """The metadata type a source file or folder holds, from its name alone."""
    parts = [part for part in re.split(r"[\\/]+", str(path).strip().strip("\"'")) if part]
    if not parts:
        return []
    name = parts[-1].lower()
    for suffix, kind in sorted(SUFFIXES.items(), key=lambda pair: -len(pair[0])):
        if name.endswith(suffix):
            return [kind]
    for part in reversed(parts):
        if part.lower() in FOLDERS:
            return [FOLDERS[part.lower()]]
    return []


# The value is one shell word: any run of plain characters and quoted stretches (`Name='A B'`).
_ORG_VALUE = re.compile(r"(?<!\S)(--target-org|--target-dev-hub|-o|--where|--values)(?:=|\s+)"
                        r"(?:\"[^\"]*\"|'[^']*'|[^\s\"'])+")


def without_org_names(command: str) -> str:
    """A command with the names and record values it carries left out: `--target-org dev-sandbox`
    is a name, not a sandbox task, and what follows `--where` or `--values` is record data, which
    has no part in finding a lesson. Both spellings (`--where X` and `--where=X`). Used for
    matching only."""
    return _ORG_VALUE.sub(r"\1", command or "")


def for_command(command: str) -> tuple[list[str], list[str]]:
    """(tasks, metadata types) a command line points to: its verbs, the `Type:Name`
    members it names, and the source files and folders it mentions."""
    tasks, types = infer(command)
    return tasks, _unique(types + command_members(command))


def command_members(command: str) -> list[str]:
    """The metadata types a command names as such: `Type:Name` members, a bare type name
    (`--metadata Layout`) and source files and folders. Not the words of the command."""
    types: list[str] = []
    for token in re.split(r"[\s,]+", command or ""):
        token = token.strip("\"'")
        head = token.split(":", 1)[0]
        if head.lower() in TYPES and (":" in token or head[:1].isupper()):
            types.append(TYPES[head.lower()])
        elif "/" in token or "\\" in token or token.lower().endswith(tuple(SUFFIXES)):
            types += types_for_path(token)
    return _unique(types)


def topic_words(words) -> list[str]:
    """The words of a topic worth matching against a lesson's text."""
    found = []
    for word in re.findall(r"[A-Za-z0-9_]{3,}", " ".join(words).lower()):
        if word not in _STOP:
            found.append(word)
    return _unique(found)


# Every task word the vocabulary gives, so that a topic word naming one is asked as that task.
TASKS = frozenset(task for _, tasks, _ in VOCABULARY for task in tasks)
_SIBILANT = ("s", "x", "z", "ch", "sh")


def word_forms(word: str) -> set[str]:
    """A lower-case word with its plural or singular ending: flow and flows, query and
    queries, box and boxes. Nothing else: report is not reporting, note is not not."""
    forms = {word}
    if word.endswith("ies") and len(word) > 4:
        forms.add(word[:-3] + "y")
    elif word.endswith("es") and word[:-2].endswith(_SIBILANT):
        forms |= {word[:-2], word[:-1]}
    elif word.endswith("s") and not word.endswith(("ss", "us", "is")) and len(word) > 3:
        forms.add(word[:-1])
    else:
        forms.add(word + ("es" if word.endswith(_SIBILANT) else "s"))
        if word.endswith("y") and len(word) > 3:
            forms.add(word[:-1] + "ies")
    return forms


def topic_terms(topic) -> tuple[list[str], list[str], list[str]]:
    """(tasks, metadata types, words) of a topic. A phrase the vocabulary knows (`flow`,
    `dashboard`, `field access`), a task's own name (`deploy`, `report`, `data-load`) and
    a type's name (`CustomField`) are asked as that task or type; only the words left over
    are matched as words, so `report dashboard` is the report task and the Dashboard type."""
    token = re.compile(r"[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*")
    task_of = lambda word: next((task for task in TASKS if task in word_forms(tag(word))), None)  # noqa: E731
    # A task written as Torque writes it (sandbox-refresh, data-load) first, then the phrases.
    words = token.findall(" ".join(str(word) for word in topic or ()))
    tasks = [task_of(word) for word in words if "-" in word and task_of(word)]
    text = " ".join(word for word in words if not ("-" in word and task_of(word)))
    more, types = infer(text)
    tasks += more
    for pattern, _, _ in _COMPILED:
        text = pattern.sub(" ", text)
    left = []
    for word in token.findall(text):
        if task_of(word):
            tasks.append(task_of(word))
        elif word.lower() in TYPES:
            types.append(TYPES[word.lower()])
        else:
            left.append(word)
    asked = {name.lower() for name in [*tasks, *types]}
    words: list[str] = []
    for word in topic_words(left):
        if not word_forms(word) & (asked | set(words)):
            words.append(word)
    return _unique(tasks), _unique(types), words
