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
_COMPILED = tuple((re.compile(r"(?<![A-Za-z0-9])(?:" + words + r")(?![A-Za-z0-9])", re.I), tasks, types)
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
_KEYWORD = re.compile(r"(?<![\w.])(?:[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+|[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+(?:\.[A-Za-z_]+)?"
                      r"|[A-Za-z]\w*__(?:c|r|mdt|e|x|b))(?!\w)")
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
    """Distinctive tokens of a text: error codes, API names, object.field references."""
    found = _unique(match.group() for match in _KEYWORD.finditer(text or ""))
    return [word for word in found if word.lower() not in TYPES][:limit]


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


def for_command(command: str) -> tuple[list[str], list[str]]:
    """(tasks, metadata types) a command line points to: its verbs, the `Type:Name`
    members it names, and the source files and folders it mentions."""
    tasks, types = infer(command)
    for token in re.split(r"[\s,]+", command or ""):
        token = token.strip("\"'")
        head = token.split(":", 1)[0]
        if head.lower() in TYPES and (":" in token or head[:1].isupper()):
            types.append(TYPES[head.lower()])
        elif "/" in token or "\\" in token or token.lower().endswith(tuple(SUFFIXES)):
            types += types_for_path(token)
    return tasks, _unique(types)


def topic_words(words) -> list[str]:
    """The words of a topic worth matching against a lesson's text."""
    found = []
    for word in re.findall(r"[A-Za-z0-9_]{3,}", " ".join(words).lower()):
        if word not in _STOP:
            found.append(word)
    return _unique(found)
