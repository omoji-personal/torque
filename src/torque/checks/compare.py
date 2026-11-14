"""Compare a submitted component with the copy retrieved from the org.

The question is whether what was submitted is what the org now holds. Statuses:

- "same": everything the source specifies is in the org with the same content, and the org copy has nothing
  else except a leaf with a default value, a known server addition (SERVER_ADDITIONS) or, for a permission set
  or profile, entries the file does not name.
- "different": something submitted is missing or changed; or the org has content the source does not, where the
  deploy replaces the definition (anywhere in a flow; inside any element the source has; an extra member of a
  collection the source lists); or the source revokes an access entry the org still grants.
- "unexplained-additions": everything submitted is there unchanged, and the org copy also has a top-level
  property the source does not have and Torque cannot explain. Not "same": someone should look at it.
- "could-not-compare": one side cannot be read.

Rules:
1. Replace semantics by default: a Metadata API deploy of a flow, field, rule, class, layout or page replaces
   that component's definition, so content only the org has is a difference, except as rule 2 says.
2. Known server additions (SERVER_ADDITIONS), per type, only at the top level and only where the source does not
   have the element; each entry names where it was seen. Anything else the org alone has at the top level of a
   type that is not rule 3 is an unexplained addition; in a flow it is a difference.
3. Merge semantics for permission sets and profiles: a deploy changes only the access entries the file names, so
   entries only the org has are expected (counted as information). An entry in the source that grants nothing is
   an expectation: met by an absent or all-false org entry, a difference when the org entry grants anything.
4. Code: exact after `code_text` (line endings, whitespace at the end of lines and of the file), the same
   function the baseline fingerprint uses.
5. Order: the Metadata API writes top-level elements in its own order, so that order is not compared; below the
   top level the order of same-tag siblings (picklist values, decision outcomes) is.

Other normalisations: a leaf present on one side only with a default value (false, 0, empty); leading and
trailing whitespace of an XML value; for a source-format object file, the fields, rules and other parts the org
returns inside the object (source format keeps them in their own files); for permission sets and profiles,
entries about components not retrieved in the same request (the Metadata API returns only those), counted as
not compared.

"same" is containment plus the rules above, not byte equality, and a grant entry is not a user's access.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .components import Component, code_text

DEFAULTISH = {"false", "0", "0.0", ""}
OBJECT_PARTS = {"fields", "validationRules", "recordTypes", "listViews", "compactLayouts", "webLinks", "fieldSets",
                "businessProcesses", "sharingReasons", "indexes"}
FLAG_TAGS = {"readable", "editable", "allowCreate", "allowDelete", "allowEdit", "allowRead", "modifyAllRecords",
             "viewAllRecords", "viewAllFields", "enabled", "visible", "default"}
NO_TAB_ACCESS = {"none", "hidden"}     # tabSettings (permission set) and tabVisibilities (profile)
# access section -> (key element, metadata type of the referenced component; None: not a component)
ACCESS_SECTIONS = {"fieldPermissions": ("field", "CustomField"), "objectPermissions": ("object", "CustomObject"),
                   "classAccesses": ("apexClass", "ApexClass"), "recordTypeVisibilities": ("recordType", "RecordType"),
                   "customPermissions": ("name", "CustomPermission"), "tabSettings": ("tab", "CustomTab"),
                   "tabVisibilities": ("tab", "CustomTab"),
                   "applicationVisibilities": ("application", "CustomApplication"),
                   "pageAccesses": ("apexPage", "ApexPage"), "flowAccesses": ("flow", "Flow"),
                   "layoutAssignments": ("layout", "Layout"), "userPermissions": ("name", None)}
MERGE_TYPES = {"PermissionSet", "Profile"}
KEYS = ("fullName", "name", "field", "object", "apexClass", "recordType", "tab", "application", "apexPage", "flow",
        "layout")


def _default_action_override(node) -> bool:
    return (node.value("type") == "Default"
            and all(c.tag in ("actionName", "formFactor", "type") and not c.children for c in node.children))


# What Salesforce writes into a component on save that the submitted file did not have: a set of the values seen,
# or a test for a repeated element. Accepted only at the top level, only where the source does not have that
# element, and only with what was seen; anything else stays visible. Add an entry only from a real retrieve.
#
# CustomObject, all seen in the live run of 2026-11-14 (Developer Edition org, API 62.0): a new custom object was
# deployed in source format with deploymentStatus, description, label, nameField, pluralLabel and sharingModel,
# then retrieved in metadata format. The org returned 30 actionOverrides of type Default (10 actions, each without
# a form factor, Large and Small), the switches below with these values, compactLayoutAssignment SYSTEM,
# externalSharingModel Private, an empty searchLayouts and visibility Public. deploymentStatus was in that source;
# the org returned it as Deployed. The false switches would pass as default leaves anyway; they are listed so the
# table says everything that run returned.
SERVER_ADDITIONS = {
    "CustomObject": {
        "actionOverrides": _default_action_override,
        "allowInChatterGroups": {"false"},
        "compactLayoutAssignment": {"SYSTEM"},
        "deploymentStatus": {"Deployed"},
        "enableActivities": {"false"},
        "enableBulkApi": {"true"},
        "enableFeeds": {"false"},
        "enableHistory": {"false"},
        "enableLicensing": {"false"},
        "enableReports": {"false"},
        "enableSearch": {"false"},
        "enableSharing": {"true"},
        "enableStreamingApi": {"true"},
        "externalSharingModel": {"Private"},
        "searchLayouts": {""},
        "visibility": {"Public"},
    },
}


@dataclass
class Outcome:
    status: str                                            # same, different, unexplained-additions, could-not-compare
    differences: list[str] = field(default_factory=list)
    not_compared: int = 0                                  # source entries about components not in this retrieve
    unexplained: list[str] = field(default_factory=list)   # top-level content only the org has, not explained
    known_additions: list[str] = field(default_factory=list)   # server additions accepted (SERVER_ADDITIONS)
    other_entries: int = 0                                 # permission set or profile entries the file does not name


def _grants(node) -> list[str]:
    """What an access entry grants: its true flags, and a tab visibility other than None or Hidden."""
    out = [c.tag for c in node.children if c.tag in FLAG_TAGS and c.text.strip().lower() == "true"]
    vis = node.value("visibility")
    if vis and vis.lower() not in NO_TAB_ACCESS:
        out.append(f"visibility {vis}")
    return out


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


def _fp(node) -> tuple:
    """Content of an element for matching, default-valued leaves left out (rule: a default leaf on one side only)."""
    return (node.tag, node.text.strip() if not node.children else "",
            tuple(_fp(c) for c in node.children if not _defaultish(c)))


def _groups(children) -> dict:
    out = defaultdict(list)
    for c in children:
        out[c.tag].append(c)
    return out


class _Run:
    def __init__(self, source: Component):
        self.type = source.type
        self.merge = source.type in MERGE_TYPES
        self.out = Outcome("same")

    def diff(self, text: str) -> None:
        self.out.differences.append(text)

    # -- the top level --------------------------------------------------------------------------------------------
    def top(self, a: dict, b: dict) -> None:
        for tag in sorted(set(a) | set(b)):
            x, y = a.get(tag, []), b.get(tag, [])
            access = tag in ACCESS_SECTIONS
            if not y:
                for c in x:
                    if not (_defaultish(c) or (access and not _grants(c))):
                        self.diff(f"{self._label(tag, c)}: only in source")
                continue
            if len(x) == 1 and len(y) == 1 and _key(x[0]) == _key(y[0]):
                self.pair(x[0], y[0], f"{tag} {_key(x[0])}" if _key(x[0]) else tag, access)
                continue
            kx, ky = {_key(c): c for c in x}, {_key(c): c for c in y}
            if x and "" not in kx and "" not in ky and len(kx) == len(x) and len(ky) == len(y):
                for k in sorted(kx):
                    if k in ky:
                        self.pair(kx[k], ky[k], f"{tag} {k}", access)
                    elif not (access and not _grants(kx[k])):
                        self.diff(f"{tag} {k}: only in source")
                for k in sorted(set(ky) - set(kx)):
                    self.org_only(tag, ky[k], f"{tag} {k}", source_has=True)
                continue
            left = Counter(_fp(c) for c in y)
            missing = 0
            for c in x:
                if left[_fp(c)] > 0:
                    left[_fp(c)] -= 1
                else:
                    missing += 1
            if missing:
                self.diff(f"{tag}: {missing} of {len(x)} in source not found unchanged in the org ({len(y)} there)")
            for c in y:
                if left[_fp(c)] > 0:
                    left[_fp(c)] -= 1
                    self.org_only(tag, c, self._label(tag, c), source_has=bool(x))

    @staticmethod
    def _label(tag: str, node) -> str:
        k = _key(node)
        return f"{tag} {k}" if k else tag

    def org_only(self, tag: str, node, label: str, source_has: bool) -> None:
        """A top-level element only the org has."""
        if _defaultish(node):
            return
        if self.merge and tag in ACCESS_SECTIONS:
            self.out.other_entries += 1
            return
        known = SERVER_ADDITIONS.get(self.type, {}).get(tag)
        if known is not None:
            ok = known(node) if callable(known) else (not node.children and not source_has
                                                      and node.text.strip() in known)
            if ok:
                self.out.known_additions.append(label)
                return
        if self.type == "Flow" or source_has:
            self.diff(f"{label}: only in org")
        else:
            self.out.unexplained.append(label)

    # -- below the top level: replace semantics, order kept ------------------------------------------------------
    def pair(self, a, b, label: str, access: bool = False) -> None:
        if access and not _grants(a) and _grants(b):
            self.diff(f"{label}: source revokes it; the org still grants {', '.join(_grants(b))}")
            return
        if not a.children and not b.children:
            if a.text.strip() != b.text.strip():
                self.diff(f"{label}: source {_short(a.text)!r}, org {_short(b.text)!r}")
            return
        if not a.children or not b.children:
            self.diff(f"{label}: differs")
            return
        x, y = _groups(a.children), _groups(b.children)
        for tag in sorted(set(x) | set(y)):
            self.inner(x.get(tag, []), y.get(tag, []), f"{label}.{tag}")

    def inner(self, x: list, y: list, label: str) -> None:
        sx = [c for c in x if not _defaultish(c)]
        sy = [c for c in y if not _defaultish(c)]
        if not y or not x:
            if sx:
                self.diff(f"{label}: only in source")
            if sy:
                self.diff(f"{label}: only in org" + (f" ({_short(sy[0].text)!r})" if not sy[0].children else "")
                          if len(sy) == 1 else f"{label}: {len(sy)} only in org")
            return
        if len(x) == 1 and len(y) == 1 and _key(x[0]) == _key(y[0]):
            self.pair(x[0], y[0], label)
            return
        kx, ky = [_key(c) for c in x], [_key(c) for c in y]
        if "" not in kx and "" not in ky and len(set(kx)) == len(kx) and len(set(ky)) == len(ky):
            bx, by = dict(zip(kx, x)), dict(zip(ky, y))
            for k in kx:
                if k in by:
                    self.pair(bx[k], by[k], f"{label} {k}")
                else:
                    self.diff(f"{label} {k}: only in source")
            for k in ky:
                if k not in bx:
                    self.diff(f"{label} {k}: only in org")
            if [k for k in kx if k in by] != [k for k in ky if k in bx]:
                self.diff(f"{label}: order differs")
            return
        fx, fy = [_fp(c) for c in x], [_fp(c) for c in y]
        if fx == fy:
            return
        if Counter(fx) == Counter(fy):
            self.diff(f"{label}: order differs")
            return
        left = Counter(fy)
        missing = 0
        for f in fx:
            if left[f] > 0:
                left[f] -= 1
            else:
                missing += 1
        if missing:
            self.diff(f"{label}: {missing} of {len(x)} in source not found unchanged in the org ({len(y)} there)")
        if sum(left.values()) and not missing:
            self.diff(f"{label}: {sum(left.values())} only in org")
        elif sum(left.values()) > missing:
            self.diff(f"{label}: {sum(left.values()) - missing} more in the org than in source")


def compare(source: Component, org: Component, retrieved: set[tuple[str, str]]) -> Outcome:
    if source.error or org.error:
        return Outcome("could-not-compare", [f"unreadable: {source.error or org.error}"])
    if source.source is not None or org.source is not None:
        if source.source is None or org.source is None:
            return Outcome("could-not-compare", ["one side is code, the other is not"])
        a, b = code_text(source.source).split("\n"), code_text(org.source).split("\n")
        if a == b:
            return Outcome("same")
        line = next((i for i, (p, q) in enumerate(zip(a, b), 1) if p != q), min(len(a), len(b)) + 1)
        return Outcome("different", [f"code differs from line {line} (source {len(a)} lines, org {len(b)})"])
    if source.node is None or org.node is None:
        return Outcome("could-not-compare", ["no definition to compare"])
    run = _Run(source)
    a, b = defaultdict(list), defaultdict(list)
    for side, node, is_org in ((a, source.node, False), (b, org.node, True)):
        for c in node.children:
            if source.type == "CustomObject" and source.fmt == "source" and c.tag in OBJECT_PARTS:
                continue
            if c.tag in ACCESS_SECTIONS:
                key_tag, typ = ACCESS_SECTIONS[c.tag]
                if typ is not None and (typ, c.value(key_tag).lower()) not in retrieved:
                    if not is_org:
                        run.out.not_compared += 1
                    continue
            side[c.tag].append(c)
    run.top(a, b)
    out = run.out
    out.status = "different" if out.differences else ("unexplained-additions" if out.unexplained else "same")
    return out
