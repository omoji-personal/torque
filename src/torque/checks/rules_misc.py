"""Validation rules, pages, names, managed components, Apex and secret-looking values."""
from __future__ import annotations

from collections import Counter, defaultdict
import re

from .components import Component
from . import apex
from .model import Context, Finding, find_ids, id_kind, namespace

USER_LITERAL = re.compile(
    r"\$(Profile\.Name|User\.(Username|Email|ProfileId|Id|Alias|FirstName|LastName)|UserRole\.(Name|DeveloperName))"
    r"\s*(=|==|<>|!=)\s*['\"]"
    r"|['\"][^'\"]*['\"]\s*(=|==|<>|!=)\s*\$(Profile\.Name|User\.\w+|UserRole\.\w+)"
    r"|\b(CONTAINS|BEGINS|CASE)\s*\(\s*\$(Profile\.Name|User\.\w+|UserRole\.\w+)\s*,\s*['\"]", re.I)
SECRET_KEY = re.compile(r"(?i)(secret|passw(or)?d|\bpwd\b|token|api[_-]?key|apikey|private[_-]?key|access[_-]?key|"
                        r"bearer|signing[_-]?key)")
SECRET_SHAPE = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----|\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{30,}|"
                          r"\bsk-[A-Za-z0-9_-]{24,}|\bxox[baprs]-[A-Za-z0-9-]{20,}|"
                          r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.|\b00D[A-Za-z0-9]{12,15}![A-Za-z0-9._-]{40,}")
SECRET_TYPES = ("CustomMetadata", "Settings", "NamedCredential", "ExternalCredential")
FLOW_NAMED = {"actionCalls", "apexPluginCalls", "assignments", "collectionProcessors", "customErrors", "decisions",
              "loops", "orchestratedStages", "recordCreates", "recordDeletes", "recordLookups", "recordRollbacks",
              "recordUpdates", "screens", "steps", "subflows", "transforms", "waits", "variables", "formulas",
              "constants", "choices", "dynamicChoiceSets", "textTemplates", "stages", "collectionChoiceSets"}
SECTION_COMPONENTS = ("flexipage:fieldSection", "flexipage:tab")


def check(comp: Component, ctx: Context) -> list[Finding]:
    out: list[Finding] = []
    if comp.type == "ValidationRule" and comp.node is not None:
        out += validation_rule(comp)
    if comp.type in ("FlexiPage", "Layout") and comp.node is not None:
        out += sections_lost(comp)
    if comp.type == "Flow" and comp.node is not None:
        out += flow_name_collisions(comp)
    if comp.type in ("ApexClass", "ApexTrigger") and comp.source is not None:
        out += apex_rules(comp)
    if comp.type in SECRET_TYPES and comp.node is not None:
        out += secrets(comp)
    ns = namespace(comp, ctx.project.namespace)
    if ns:
        out.append(Finding("managed.component-in-change", "warning", comp,
                           f"This component belongs to the managed package with namespace {ns}.",
                           "A deploy can change little of a packaged component and an edit can break the next package "
                           "upgrade; extend beside it or use the package's own settings, and remove it from the change "
                           "unless the package allows this edit.", line=comp.line, subject="namespace"))
    return out


def validation_rule(comp: Component) -> list[Finding]:
    n = comp.node
    out = []
    formula = n.child("errorConditionFormula")
    text = formula.text if formula is not None else ""
    for token in find_ids(text):
        out.append(Finding("rule.hard-coded-id", "warning", comp,
                           f"The formula holds a hard-coded {id_kind(token)}, which means something else in another org.",
                           "Use a custom permission ($Permission), custom metadata or a stable name instead of the Id.",
                           line=formula.line, subject=f"id:{token}"))
    if USER_LITERAL.search(text):
        out.append(Finding("rule.profile-or-user-literal", "note", comp,
                           "The formula singles out a profile, role or user by name or Id.",
                           "Bypass through a custom permission ($Permission.Name) assigned by permission set; names "
                           "change and differ between orgs.", line=formula.line, subject="user-literal"))
    if not n.value("errorMessage"):
        out.append(Finding("rule.no-error-message", "warning", comp, "The validation rule has no error message.",
                           "Write a message that tells the user what to change to save the record.",
                           line=n.line, subject="message"))
    return out


def section_labels(node) -> list[str]:
    """Labels of field sections and tabs on a Lightning page, or of sections on a page layout."""
    labels = []
    for c in node.iter():
        if c.tag == "componentInstance" and c.value("componentName") in SECTION_COMPONENTS:
            for p in c.all("componentInstanceProperties"):
                if p.value("name") == "label" and p.value("value"):
                    labels.append(p.value("value"))
        elif c.tag == "layoutSections" and c.value("label"):
            labels.append(c.value("label"))
    return labels


def sections_lost(comp: Component) -> list[Finding]:
    if comp.baseline is None or comp.baseline.node is None:
        return []
    now, before = Counter(section_labels(comp.node)), Counter(section_labels(comp.baseline.node))
    lost = before - now
    if sum(now.values()) >= sum(before.values()) or not lost:
        return []
    shown = ", ".join(sorted(lost.elements())[:6])
    return [Finding("page.sections-lost", "warning", comp,
                    f"The page has {sum(now.values())} labelled sections or tabs; the baseline had "
                    f"{sum(before.values())}. Missing: {shown}.",
                    "Restore the labels from the baseline unless removing them was intended; a bulk edit of label "
                    "properties removes section headers too.", line=comp.line, subject="sections")]


def flow_name_collisions(comp: Component) -> list[Finding]:
    names: dict[str, list] = defaultdict(list)
    for n in comp.node.iter():
        parent = n.parent.tag if n.parent is not None else ""
        named = (n.tag in FLOW_NAMED and n.parent is comp.node) or (n.tag == "rules" and parent == "decisions") \
            or (n.tag in ("fields", "actions") and _under(n, "screens")) or n.tag == "waitEvents"
        if named and n.value("name"):
            names[n.value("name").lower()].append(n)
    out = []
    for key, nodes in sorted(names.items()):
        if len(nodes) < 2:
            continue
        spelled = sorted({x.value("name") for x in nodes})
        what = (f"names that differ only by case ({', '.join(spelled)})" if len(spelled) > 1
                else f"the name {spelled[0]} {len(nodes)} times")
        out.append(Finding("name.case-collision", "warning", comp,
                           f"The flow uses {what}; Flow names are not case-sensitive, so the deploy fails with "
                           "'Duplicate developer name'.", "Give each element and resource its own name.",
                           line=nodes[1].line, subject=f"flow-name:{key}"))
    return out


def _under(node, tag: str) -> bool:
    p = node.parent
    while p is not None:
        if p.tag == tag:
            return True
        p = p.parent
    return False


def component_collisions(in_scope: list[Component], everything: list[Component]) -> list[Finding]:
    groups: dict[tuple, set[str]] = defaultdict(set)
    for c in everything:
        groups[c.key].add(c.name)
    out, done = [], set()
    for c in in_scope:
        names = groups.get(c.key, set())
        if len(names) > 1 and c.key not in done:
            done.add(c.key)
            out.append(Finding("name.case-collision", "warning", c,
                               f"Two {c.type} components have names that differ only by case: {', '.join(sorted(names))}.",
                               "Salesforce treats them as one name; keep one spelling and merge or rename the other.",
                               line=c.line, subject="component-name"))
    return out


def apex_rules(comp: Component) -> list[Finding]:
    info = apex.read(comp.source)
    out = []
    if comp.type == "ApexClass" and info.kind == "class" and not info.is_test and info.sharing is None:
        out.append(Finding("code.no-sharing-declaration", "note", comp,
                           "The class does not declare its sharing.",
                           "Declare with sharing (the usual choice), inherited sharing, or without sharing with a "
                           "comment saying why.", line=info.decl_line, subject="sharing"))
    if info.is_test and info.test_methods and not info.has_assert:
        out.append(Finding("code.test-without-assert", "warning", comp,
                           "The test class has test methods and no assertion, so it proves only that the code runs.",
                           "Assert the outcome the requirement names (Assert.areEqual and the like). If assertions live "
                           "in a helper, ignore this rule for the class in torque-check.yml.",
                           line=info.decl_line, subject="assert"))
    if info.is_test and info.see_all_data_line:
        out.append(Finding("code.see-all-data", "warning", comp,
                           "The test uses SeeAllData=true, so it depends on the org's records.",
                           "Create the test data in the test (a test data factory) and remove SeeAllData.",
                           line=info.see_all_data_line, subject="see-all-data"))
    return out


def _pairs(node):
    """(key, value, line) for every leaf, and for name/value pairs such as custom metadata values."""
    for n in node.iter():
        if not n.children and n.text.strip():
            yield n.tag, n.text.strip(), n.line
        for k_tag, v_tag in (("field", "value"), ("name", "value"), ("parameterName", "parameterValue")):
            k, v = n.child(k_tag), n.child(v_tag)
            if k is not None and v is not None and k.text.strip() and v.text.strip():
                yield k.text.strip(), v.text.strip(), v.line


def _looks_secret(value: str) -> bool:
    if len(value) < 12 or " " in value or value.startswith(("{!", "http://", "https://", "$")):
        return False
    return any(ch.isdigit() for ch in value) and any(ch.isalpha() for ch in value)


def secrets(comp: Component) -> list[Finding]:
    out, seen = [], set()
    for key, value, line in _pairs(comp.node):
        hit = bool(SECRET_SHAPE.search(value)) or (bool(SECRET_KEY.search(key)) and _looks_secret(value))
        if hit and key not in seen:
            seen.add(key)
            out.append(Finding("secret.looks-like-secret", "warning", comp,
                               f"{key} holds a value that looks like a secret (the value is not shown).",
                               "Never keep secrets in metadata: move it to a named or external credential, remove it "
                               "from the file and its history, and rotate it if it was shared.",
                               line=line, subject=f"secret:{key}"))
    return out
