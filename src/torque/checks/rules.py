"""The rule table and the run: which rules apply to which component, and inherited problems."""
from __future__ import annotations

from .components import Component
from .model import Context, Finding, RANK, version
from . import rules_access, rules_doc, rules_flow, rules_misc

# id: (default severity, what it inspects). Severity can be lower for a variant (noted in docs/checks.md).
RULES = {
    "file.unreadable": ("error", "a metadata file that is not well-formed XML or cannot be read"),
    "access.field-without-grant": ("error", "a new custom field that takes field-level security, with no "
                                            "fieldPermissions entry in any permission set in the change"),
    "access.grant-only-in-profile": ("warning", "a new custom field granted only by a profile in the change"),
    "access.formula-field-editable": ("error", "an editable fieldPermissions entry for a formula or roll-up field"),
    "access.profile-in-change": ("warning", "a profile file in the change"),
    "access.powerful-permission": ("warning", "Modify All Data, View All Data, Manage Users, View All, Modify All or "
                                              "View All Fields in a permission set"),
    "doc.description-missing": ("warning", "no description on a custom field, custom object, flow, validation rule, "
                                           "permission set or group, record type or custom permission"),
    "doc.description-placeholder": ("note", "a description that repeats the label or name, or is TODO, test, ..."),
    "doc.too-long": ("error", "a description or help text over the length at which the deploy fails"),
    "doc.help-text-missing": ("note", "no help text on a custom field a user fills in"),
    "doc.flow-elements-undescribed": ("note", "flow elements (new ones, when a baseline exists) without a description"),
    "doc.description-names-version": ("note", "a flow description that names a version number"),
    "doc.apex-header-missing": ("note", "a non-test Apex class or trigger without a comment above its declaration"),
    "flow.no-fault-path": ("warning", "Create, Update, Delete Records and actions without a fault connector "
                                      "(note for Get Records and subflows)"),
    "flow.work-in-loop": ("error", "Get, Create, Update, Delete Records, actions or subflows inside a loop body"),
    "flow.no-entry-conditions": ("warning", "a record-triggered flow without entry conditions"),
    "flow.reruns-on-every-update": ("warning", "an update-triggered flow with side effects that runs again on every "
                                               "save meeting its conditions"),
    "flow.after-save-same-record": ("note", "an after-save flow whose only change updates the triggering record"),
    "flow.hard-coded-id": ("warning", "a 15 or 18 character record Id in a flow value, formula or text template"),
    "flow.api-version-behind": ("note", "a flow apiVersion below the project's sourceApiVersion (error when the "
                                        "change lowers it)"),
    "flow.screen-action-without-trigger": ("warning", "a screen action no trigger runs (note: an unknown trigger "
                                                      "event name)"),
    "flow.default-names": ("note", "default or copied element names and labels"),
    "flow.active-in-source": ("note", "a flow file that asks for Active (said once per report)"),
    "flow.system-mode-without-sharing": ("note", "system mode without sharing with no reason in the description"),
    "flow.choice-compared-to-literal": ("warning", "a formula comparing a choice screen field to a text literal"),
    "flow.lookup-missing-first-record-only": ("warning", "Get Records storing output automatically without "
                                                         "getFirstRecordOnly"),
    "rule.hard-coded-id": ("warning", "a record Id in a validation rule formula"),
    "rule.profile-or-user-literal": ("note", "a validation rule that singles out a profile, role or user literal"),
    "rule.no-error-message": ("warning", "a validation rule without an error message"),
    "page.sections-lost": ("warning", "a Lightning page or layout with fewer labelled sections than its baseline"),
    "name.case-collision": ("warning", "two components, or two names inside a flow, that differ only by case"),
    "managed.component-in-change": ("warning", "a managed-package (namespaced) component in the change"),
    "code.no-sharing-declaration": ("note", "an Apex class without with, without or inherited sharing"),
    "code.test-without-assert": ("warning", "an Apex test class with test methods and no assertion"),
    "code.see-all-data": ("warning", "an Apex test with SeeAllData=true"),
    "secret.looks-like-secret": ("warning", "a secret-looking value in custom metadata, settings or credential files "
                                            "(file and key only)"),
}


def build_context(project, everything: list[Component], in_scope: list[Component], grant_scope: list[Component],
                  baseline: list[Component] | None) -> Context:
    ctx = Context(project=project, everything=everything, grant_scope=grant_scope, baseline_mode=baseline is not None)
    for c in list(baseline or []) + everything:     # the current definition wins
        if c.type == "CustomField":
            ctx.fields[c.name.lower()] = c
        elif c.type == "CustomObject":
            ctx.objects[c.name.lower()] = c
    return ctx


def component_findings(comp: Component, ctx: Context) -> list[Finding]:
    if comp.error:
        return [Finding("file.unreadable", "error", comp, f"The file could not be read: {comp.error}.",
                        "Fix the file (or retrieve it again) and rerun the check; no other rule ran on it.",
                        line=comp.error_line, subject="unreadable")]
    out: list[Finding] = []
    for rule in rules_access.RULES:
        out += rule(comp, ctx)
    out += rules_doc.check(comp, ctx)
    out += rules_flow.check(comp, ctx)
    out += rules_misc.check(comp, ctx)
    return out


def run(in_scope: list[Component], ctx: Context) -> list[Finding]:
    findings: list[Finding] = []
    for comp in in_scope:
        found = component_findings(comp, ctx)
        base = comp.baseline
        if base is not None:
            before = {(f.rule, f.subject) for f in component_findings(base, ctx)}
            for f in found:
                if f.inherited is None:
                    f.inherited = (f.rule, f.subject) in before
        elif ctx.baseline_mode and comp.status == "new":
            for f in found:
                f.inherited = False
        findings += found
    findings += rules_misc.component_collisions(in_scope, ctx.everything)
    return findings


def order(findings: list[Finding]) -> list[Finding]:
    worst: dict[int, int] = {}
    for f in findings:
        worst[id(f.comp)] = min(worst.get(id(f.comp), 9), RANK[f.severity])
    return sorted(findings, key=lambda f: (worst[id(f.comp)], f.comp.type, f.comp.name.lower(), f.path,
                                           RANK[f.severity], f.line or 0, f.rule))
