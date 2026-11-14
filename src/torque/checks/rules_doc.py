"""Documentation rules: descriptions, help text, flow element descriptions, Apex headers."""
from __future__ import annotations

import re

from .components import Component, is_custom
from .model import Context, Finding, namespace, norm
from . import apex

DESCRIBED = ("CustomField", "CustomObject", "Flow", "ValidationRule", "PermissionSet", "PermissionSetGroup",
             "RecordType", "CustomPermission")
PLACEHOLDERS = {"todo", "tbd", "tba", "test", "testing", "description", "desc", "na", "none", "xxx", "fixme",
                "placeholder", "wip", "temp"}
# Limits past which a deploy fails (field evidence: descriptions over the limit failed two deploys).
LIMITS = {("CustomField", "description"): 1000, ("CustomField", "inlineHelpText"): 510,
          ("PermissionSet", "description"): 255, ("ValidationRule", "description"): 255}
FLOW_ELEMENTS = ("actionCalls", "apexPluginCalls", "assignments", "collectionProcessors", "customErrors",
                 "decisions", "loops", "orchestratedStages", "recordCreates", "recordDeletes", "recordLookups",
                 "recordRollbacks", "recordUpdates", "screens", "steps", "subflows", "transforms", "waits")
NAMES_VERSION = re.compile(r"(?i)(?<!api )\b(?:flow\s+)?(?:version|ver\.?|v)\s?\d+(?![\d.])")
WHAT = {"CustomField": "field", "CustomObject": "object", "Flow": "flow", "ValidationRule": "validation rule",
        "PermissionSet": "permission set", "PermissionSetGroup": "permission set group",
        "RecordType": "record type", "CustomPermission": "custom permission"}
WHY = {"CustomField": "why the field exists and what writes it",
       "CustomObject": "what one record represents and who uses it",
       "Flow": "what starts it, what it changes and why",
       "ValidationRule": "the business rule it enforces and who may bypass it",
       "PermissionSet": "who gets it and what it lets them do",
       "PermissionSetGroup": "which role it serves",
       "RecordType": "when to use this record type",
       "CustomPermission": "what it switches on and where it is checked"}


def _documented(comp: Component, ctx: Context) -> bool:
    if comp.node is None or comp.type not in DESCRIBED or namespace(comp, ctx.project.namespace):
        return False
    if comp.type in ("CustomField", "CustomObject"):
        own = comp.name.rsplit(".", 1)[-1]
        return is_custom(own)
    return True


def _field_object(comp: Component, ctx: Context):
    return ctx.objects.get(comp.name.split(".", 1)[0].lower())


def user_fills_in(comp: Component, ctx: Context) -> bool:
    """A custom field a person enters: not computed, not on a type admins configure."""
    n = comp.node
    obj = comp.name.split(".", 1)[0]
    if n.value("formula") or n.value("type") in ("Summary", "AutoNumber"):
        return False
    if obj.lower().endswith(("__mdt", "__e")):
        return False
    o = _field_object(comp, ctx)
    return not (o is not None and o.node is not None and o.node.value("customSettingsType"))


def check(comp: Component, ctx: Context) -> list[Finding]:
    out: list[Finding] = []
    if _documented(comp, ctx):
        out += _description(comp)
        if comp.type == "CustomField":
            out += _help_text(comp, ctx)
        if comp.type == "Flow":
            out += _flow_elements(comp)
            out += _names_version(comp)
    out += _too_long(comp)
    if comp.type in ("ApexClass", "ApexTrigger") and comp.source is not None:
        out += _apex_header(comp)
    return out


def _description(comp: Component) -> list[Finding]:
    n = comp.node
    desc = n.value("description")
    what, why = WHAT[comp.type], WHY[comp.type]
    line = n.child("description").line if n.child("description") is not None else n.line
    if not desc:
        return [Finding("doc.description-missing", "warning", comp, f"The {what} has no description.",
                        f"Add a description: {why}.", line=line, subject="description")]
    label = n.value("label") or n.value("masterLabel")
    own = comp.name.rsplit(".", 1)[-1]
    d = norm(desc)
    if (d in PLACEHOLDERS or d == norm(label) or d == norm(re.sub(r"__\w+$", "", own))
            or desc.lower().startswith(("todo", "tbd", "fixme"))):
        return [Finding("doc.description-placeholder", "note", comp,
                        f"The {what}'s description only repeats its name or is a placeholder.",
                        f"Replace it with {why}.", line=line, subject="description")]
    return []


def _help_text(comp: Component, ctx: Context) -> list[Finding]:
    if comp.node.value("inlineHelpText") or not user_fills_in(comp, ctx):
        return []
    return [Finding("doc.help-text-missing", "note", comp, "The field has no help text.",
                    "Add help text that tells the user what to enter (the description is for admins; help text is "
                    "what users see).", line=comp.node.line, subject="inlineHelpText")]


def flow_elements(node) -> list:
    return [c for c in node.children if c.tag in FLOW_ELEMENTS and c.value("name")]


def _flow_elements(comp: Component) -> list[Finding]:
    elements = flow_elements(comp.node)
    known = None
    if comp.baseline is not None and comp.baseline.node is not None:
        known = {e.value("name").lower() for e in flow_elements(comp.baseline.node)}
        elements = [e for e in elements if e.value("name").lower() not in known]
    bare = [e for e in elements if not e.value("description")]
    if not bare:
        return []
    names = ", ".join(e.value("name") for e in bare[:5]) + (f" and {len(bare) - 5} more" if len(bare) > 5 else "")
    scope = "elements new in this change" if known is not None else "elements"
    f = Finding("doc.flow-elements-undescribed", "note", comp,
                f"{len(bare)} of {len(elements)} flow {scope} have no description: {names}.",
                "Describe what each element does and why, in its Description field.",
                line=bare[0].line, subject="elements")
    f.match_lines = {e.line for e in bare}
    if known is not None:
        f.inherited = False
    return [f]


def _names_version(comp: Component) -> list[Finding]:
    desc = comp.node.value("description")
    if not desc or not NAMES_VERSION.search(desc):
        return []
    return [Finding("doc.description-names-version", "note", comp,
                    "The flow description names a version number, which goes stale with the next version.",
                    "Describe what the flow does; keep version history in the change record or release notes.",
                    line=comp.node.child("description").line, subject="description-version")]


def _too_long(comp: Component) -> list[Finding]:
    out = []
    if comp.node is None:
        return out
    for (typ, tag), limit in LIMITS.items():
        if comp.type != typ:
            continue
        c = comp.node.child(tag)
        if c is not None and len(c.text.strip()) > limit:
            out.append(Finding("doc.too-long", "error", comp,
                               f"{tag} is {len(c.text.strip())} characters; the limit is {limit}, so the deploy fails.",
                               f"Shorten it to {limit} characters or fewer.", line=c.line, subject=tag))
    return out


def _apex_header(comp: Component) -> list[Finding]:
    info = apex.read(comp.source)
    if info.is_test or info.kind is None or info.has_header:
        return []
    what = "trigger" if comp.type == "ApexTrigger" else info.kind
    return [Finding("doc.apex-header-missing", "note", comp, f"The {what} has no header comment.",
                    "Add a comment above the declaration: what it does, what calls it, and why code was chosen over "
                    "a declarative option.", line=info.decl_line, subject="header")]
