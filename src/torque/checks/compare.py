"""Compare a submitted component with the copy retrieved from the org.

Only known serialisation differences are normalised:
- the order of the component's top-level elements (the Metadata API writes its own order; order inside an
  element, such as picklist values or decision outcomes, is kept and compared);
- a leaf element present on one side only with a default value (false, 0 or empty), which the org writes and a
  source file may leave out;
- permission entries that grant nothing (all flags false) on either side;
- for a source-format object file, the fields, rules and other parts the org returns inside the object, which
  source format keeps in their own files;
- for permission sets and profiles, entries about components that were not retrieved in the same request (the
  Metadata API returns only those), which are counted and reported as not compared.
Anything else that differs is a difference. A pair that cannot be read is "could not compare", never "same".
"""
from __future__ import annotations

from collections import defaultdict

from .components import Component

DEFAULTISH = {"false", "0", "0.0", ""}
OBJECT_PARTS = {"fields", "validationRules", "recordTypes", "listViews", "compactLayouts", "webLinks", "fieldSets",
                "businessProcesses", "sharingReasons", "indexes"}
FLAG_TAGS = {"readable", "editable", "allowCreate", "allowDelete", "allowEdit", "allowRead", "modifyAllRecords",
             "viewAllRecords", "viewAllFields", "enabled", "visible", "default"}
# permission-set section -> (key element, metadata type of the referenced component)
ACCESS_SECTIONS = {"fieldPermissions": ("field", "CustomField"), "objectPermissions": ("object", "CustomObject"),
                   "classAccesses": ("apexClass", "ApexClass"), "recordTypeVisibilities": ("recordType", "RecordType"),
                   "customPermissions": ("name", "CustomPermission"), "tabSettings": ("tab", "CustomTab"),
                   "applicationVisibilities": ("application", "CustomApplication"),
                   "pageAccesses": ("apexPage", "ApexPage"), "flowAccesses": ("flow", "Flow"),
                   "layoutAssignments": ("layout", "Layout")}
KEYS = ("fullName", "name", "field", "object", "apexClass", "recordType", "tab", "application", "apexPage", "flow",
        "layout")


def _grants_nothing(node) -> bool:
    flags = [c for c in node.children if c.tag in FLAG_TAGS]
    return bool(flags) and all(c.text.strip().lower() == "false" for c in flags)


def _key(node) -> str:
    for k in KEYS:
        v = node.value(k)
        if v:
            return v
    return ""


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= 60 else text[:57] + "..."


def compare(source: Component, org: Component, retrieved: set[tuple[str, str]]) -> tuple[str, list[str], int]:
    """(same | different | could-not-compare, differences, entries not compared)."""
    if source.error or org.error:
        return "could-not-compare", [f"unreadable: {source.error or org.error}"], 0
    if source.source is not None or org.source is not None:
        if source.source is None or org.source is None:
            return "could-not-compare", ["one side is code, the other is not"], 0
        same = "".join(source.source.split()) == "".join(org.source.split())
        return ("same" if same else "different"), ([] if same else ["code body differs"]), 0
    if source.node is None or org.node is None:
        return "could-not-compare", ["no definition to compare"], 0
    skipped = 0
    a, b = defaultdict(list), defaultdict(list)
    for side, node, other in ((a, source.node, False), (b, org.node, True)):
        for c in node.children:
            if source.type == "CustomObject" and source.fmt == "source" and c.tag in OBJECT_PARTS:
                continue
            if c.tag in ACCESS_SECTIONS:
                key_tag, typ = ACCESS_SECTIONS[c.tag]
                if _grants_nothing(c):
                    continue
                key = c.value(key_tag)
                if (typ, key.lower()) not in retrieved:
                    if not other:
                        skipped += 1
                    continue
            side[c.tag].append(c)
    diffs = []
    for tag in sorted(set(a) | set(b)):
        x, y = a.get(tag, []), b.get(tag, [])
        if len(x) == len(y) == 1 and not x[0].children and not y[0].children:
            if x[0].text.strip() != y[0].text.strip():
                diffs.append(f"{tag}: source {_short(x[0].text)!r}, org {_short(y[0].text)!r}")
            continue
        if not y and all(not c.children and c.text.strip().lower() in DEFAULTISH for c in x):
            continue
        if not x and all(not c.children and c.text.strip().lower() in DEFAULTISH for c in y):
            continue
        fx, fy = sorted(repr(c.fingerprint()) for c in x), sorted(repr(c.fingerprint()) for c in y)
        if fx == fy:
            continue
        if not x or not y:
            diffs.append(f"{tag}: only in {'the org' if not x else 'source'}")
            continue
        kx, ky = {_key(c): repr(c.fingerprint()) for c in x}, {_key(c): repr(c.fingerprint()) for c in y}
        if "" in kx or "" in ky or len(kx) != len(x) or len(ky) != len(y):
            diffs.append(f"{tag}: {len(x)} in source, {len(y)} in the org, content differs")
            continue
        for k in sorted(set(kx) | set(ky)):
            if k not in ky:
                diffs.append(f"{tag} {k}: only in source")
            elif k not in kx:
                diffs.append(f"{tag} {k}: only in the org")
            elif kx[k] != ky[k]:
                diffs.append(f"{tag} {k}: differs")
    return ("same" if not diffs else "different"), diffs, skipped
