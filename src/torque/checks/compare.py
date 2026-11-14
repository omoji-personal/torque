"""Compare a submitted component with the copy retrieved from the org.

The question is whether what was submitted is what the org now holds, so the comparison is one-sided: every
element the source specifies must be in the org with the same content. What only the org has (the defaults
Salesforce adds on save, such as an object's sharing and API switches, or something another person changed) is
listed apart and does not make the component "different". A first live run showed why: every new object and
permission set read as different for that reason alone.

Outcomes: "same" (nothing differs and the org has nothing more), "same-org-has-more" (everything submitted is
there unchanged; the org copy has more), "different" (something submitted is missing or changed in the org). A
pair that cannot be read is "could not compare", never "same".

Only known serialisation differences are normalised:
- the order of elements of different tags (the Metadata API writes its own order; the content of each element,
  including the order inside it, is compared);
- a leaf element present on one side only with a default value (false, 0 or empty), which the org writes and a
  source file may leave out;
- permission entries that grant nothing (all flags false) on either side;
- for a source-format object file, the fields, rules and other parts the org returns inside the object, which
  source format keeps in their own files;
- for permission sets and profiles, entries about components that were not retrieved in the same request (the
  Metadata API returns only those), which are counted and reported as not compared.
"""
from __future__ import annotations

from collections import Counter, defaultdict

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


def _defaultish(node) -> bool:
    return not node.children and node.text.strip().lower() in DEFAULTISH


def _pair(a, b, label: str, diffs: list[str], extras: list[str]) -> None:
    """One source element against the org's element of the same tag (and key)."""
    if not a.children and not b.children:
        if a.text.strip() != b.text.strip():
            diffs.append(f"{label}: source {_short(a.text)!r}, org {_short(b.text)!r}")
        return
    if not a.children or not b.children:
        diffs.append(f"{label}: differs")
        return
    x, y = defaultdict(list), defaultdict(list)
    for c in a.children:
        x[c.tag].append(c)
    for c in b.children:
        y[c.tag].append(c)
    _contain(x, y, label + ".", diffs, extras)


def _in_order(wanted: list, having: list) -> bool:
    """True when the items of wanted appear in having in the same relative order."""
    it = iter(having)
    return all(any(w == h for h in it) for w in wanted)


def _contain(a: dict, b: dict, prefix: str, diffs: list[str], extras: list[str]) -> None:
    """Everything in a (source) must be in b (org) with the same content; what only b has goes to extras.
    Below the component's top level the order of same-tag siblings means something (picklist values, decision
    outcomes), so there it must be kept; at the top level the Metadata API writes its own order."""
    ordered = bool(prefix)
    for tag in sorted(set(a) | set(b)):
        x, y = a.get(tag, []), b.get(tag, [])
        label = prefix + tag
        if not x:
            if not all(_defaultish(c) for c in y):
                extras.append(label if len(y) == 1 else f"{label} ({len(y)})")
            continue
        if not y:
            if not all(_defaultish(c) for c in x):
                diffs.append(f"{label}: only in source")
            continue
        if len(x) == 1 and len(y) == 1 and _key(x[0]) == _key(y[0]):
            _pair(x[0], y[0], label, diffs, extras)
            continue
        kx, ky = {_key(c): c for c in x}, {_key(c): c for c in y}
        if "" not in kx and "" not in ky and len(kx) == len(x) and len(ky) == len(y):
            for k in sorted(kx):
                if k in ky:
                    _pair(kx[k], ky[k], f"{label} {k}", diffs, extras)
                else:
                    diffs.append(f"{label} {k}: only in source")
            extras.extend(f"{label} {k}" for k in sorted(set(ky) - set(kx)))
            if ordered and not _in_order([k for k in kx if k in ky], list(ky)):
                diffs.append(f"{label}: order differs")
            continue
        # No usable key: each source item must appear unchanged in the org.
        fx, fy = [repr(c.fingerprint()) for c in x], [repr(c.fingerprint()) for c in y]
        left = Counter(fy)
        missing = 0
        for f in fx:
            if left[f] > 0:
                left[f] -= 1
            else:
                missing += 1
        if missing:
            diffs.append(f"{label}: {missing} of {len(x)} in source not found unchanged in the org ({len(y)} there)")
            continue
        if ordered and not _in_order(fx, fy):
            diffs.append(f"{label}: order differs")
        if sum(left.values()):
            extras.append(f"{label} ({sum(left.values())} more)")


def compare(source: Component, org: Component,
            retrieved: set[tuple[str, str]]) -> tuple[str, list[str], int, list[str]]:
    """(same | same-org-has-more | different | could-not-compare, differences, entries not compared, what only
    the org has)."""
    if source.error or org.error:
        return "could-not-compare", [f"unreadable: {source.error or org.error}"], 0, []
    if source.source is not None or org.source is not None:
        if source.source is None or org.source is None:
            return "could-not-compare", ["one side is code, the other is not"], 0, []
        same = "".join(source.source.split()) == "".join(org.source.split())
        return ("same" if same else "different"), ([] if same else ["code body differs"]), 0, []
    if source.node is None or org.node is None:
        return "could-not-compare", ["no definition to compare"], 0, []
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
    diffs: list[str] = []
    extras: list[str] = []
    _contain(a, b, "", diffs, extras)
    status = "different" if diffs else ("same-org-has-more" if extras else "same")
    return status, diffs, skipped, extras
