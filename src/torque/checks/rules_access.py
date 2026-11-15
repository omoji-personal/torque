"""Access rules: new fields without a grant, editable formula grants, profiles, powerful permissions."""
from __future__ import annotations

from .components import Component, is_custom
from .model import Context, Finding, namespace

POWERFUL_USER = {"ModifyAllData": "Modify All Data", "ViewAllData": "View All Data", "ManageUsers": "Manage Users",
                 "AuthorApex": "Author Apex", "CustomizeApplication": "Customize Application",
                 "ModifyMetadata": "Modify Metadata Through Metadata API Functions",
                 "ManageProfilesPermissionsets": "Manage Profiles and Permission Sets",
                 "AssignPermissionSets": "Assign Permission Sets"}
POWERFUL_OBJECT = {"modifyAllRecords": "Modify All", "viewAllRecords": "View All", "viewAllFields": "View All Fields"}
GRANT_TYPES = ("PermissionSet", "Profile")


def grants(comp: Component) -> dict[str, tuple[bool, bool, int]]:
    """field (lower) -> (readable, editable, line) for a permission set or profile."""
    out = {}
    if comp.node is None:
        return out
    for fp in comp.node.all("fieldPermissions"):
        name = fp.value("field").lower()
        if name:
            out[name] = (fp.flag("readable"), fp.flag("editable"), fp.line)
    return out


def _keys(field_name: str) -> set[str]:
    obj, _, fld = field_name.lower().partition(".")
    keys = {field_name.lower()}
    if obj in ("activity", "task", "event"):
        keys |= {f"activity.{fld}", f"task.{fld}", f"event.{fld}"}
    return keys


def needs_grant(comp: Component, ctx: Context) -> bool:
    """A custom field that takes field-level security."""
    n = comp.node
    obj, _, fld = comp.name.partition(".")
    if n is None or not is_custom(fld) or namespace(comp, ctx.project.namespace):
        return False
    if n.flag("required") or n.value("type") == "MasterDetail":
        return False
    if obj.lower().endswith(("__mdt", "__e")):
        return False
    o = ctx.objects.get(obj.lower())
    return not (o is not None and o.node is not None and o.node.value("customSettingsType"))


def _who_grants(field_name: str, sets: list[Component]) -> list[Component]:
    keys = _keys(field_name)
    return [s for s in sets if any(k in grants(s) and (grants(s)[k][0] or grants(s)[k][1]) for k in keys)]


def field_without_grant(comp: Component, ctx: Context) -> list[Finding]:
    if comp.type != "CustomField" or comp.status not in ("new", "unknown", "failed") or not needs_grant(comp, ctx):
        return []
    in_scope = ctx.grant_scope
    by_set = _who_grants(comp.name, [s for s in in_scope if s.type == "PermissionSet"])
    if by_set:
        return []
    by_profile = _who_grants(comp.name, [s for s in in_scope if s.type == "Profile"])
    sets = sorted(s.name for s in in_scope if s.type == "PermissionSet")
    listed = ", ".join(sets) if sets else "none"
    maybe = " (no change scope was given, so this field may not be new)" if comp.status == "unknown" else ""
    if by_profile:
        return [Finding("access.grant-only-in-profile", "warning", comp,
                        f"Only a profile {ctx.scope_word} grants {comp.name} ({', '.join(p.name for p in by_profile)}){maybe}.",
                        "Grant it in a permission set as well, so access does not depend on deploying a profile. "
                        f"Permission sets {ctx.scope_word}: {listed}.", line=comp.line, subject="grant")]
    elsewhere = [s for s in _who_grants(comp.name, [c for c in ctx.everything if c.type in GRANT_TYPES])
                 if s not in in_scope]
    hint = (f" {elsewhere[0].type} {elsewhere[0].name} under the checked folder grants it but is not in this change; "
            "add it to the deploy." if elsewhere else "")
    # An error only when the change is known (a deploy result or a baseline): then a grant outside it does not ship.
    # With no scope the field may not be new; with --only or files the grant may be in a component not named,
    # and when the project around them holds one, that is a note.
    known = ctx.scope_mode in ("changed-from", "baseline", "")
    severity = "error" if known else ("note" if elsewhere and ctx.scope_mode in ("only", "files") else "warning")
    return [Finding("access.field-without-grant", severity, comp,
                    f"No permission set {ctx.scope_word} grants {comp.name}, so nobody but an admin can see it{maybe}.",
                    f"Add a fieldPermissions entry for {comp.name} (readable true; editable true only if users enter "
                    f"it) to the permission set its users get. Permission sets {ctx.scope_word}: {listed}.{hint}",
                    line=comp.line, subject="grant")]


def formula_field_editable(comp: Component, ctx: Context) -> list[Finding]:
    if comp.type not in GRANT_TYPES or comp.node is None:
        return []
    out = []
    for fp in comp.node.all("fieldPermissions"):
        if not fp.flag("editable"):
            continue
        name = fp.value("field")
        if not fp.flag("readable"):
            out.append(Finding("access.formula-field-editable", "error", comp,
                               f"{name} is granted editable but not readable; the deploy fails on it (edit needs "
                               "read).", f"Set readable to true for {name}, or editable to false.", line=fp.line,
                               subject=f"edit-without-read:{name.lower()}"))
        definition = next((ctx.fields[k] for k in sorted(_keys(name)) if k in ctx.fields), None)
        if definition is None or definition.node is None:
            continue
        kind = ("formula" if definition.node.value("formula") else
                {"Summary": "roll-up summary", "AutoNumber": "auto-number"}.get(definition.node.value("type")))
        if kind:
            article = "an" if kind[0] in "aeiou" else "a"
            out.append(Finding("access.formula-field-editable", "error", comp,
                               f"{name} is {article} {kind} field granted as editable; the deploy fails on it.",
                               f"Set editable to false for {name} (keep readable true).", line=fp.line,
                               subject=f"editable:{name.lower()}"))
    return out


def profile_in_change(comp: Component, ctx: Context) -> list[Finding]:
    if comp.type != "Profile":
        return []
    return [Finding("access.profile-in-change", "warning", comp, "A profile is part of this change.",
                    "A profile deploy rewrites that profile's access to every component the file names, for everyone "
                    "who has it. Grant access through a permission set instead, or keep only the sections you mean to "
                    "change.", line=comp.line, subject="profile")]


def powerful_permission(comp: Component, ctx: Context) -> list[Finding]:
    if comp.type not in GRANT_TYPES or comp.node is None:
        return []
    if comp.type == "Profile":       # one finding per profile: an administrator profile enables many of these
        named = [POWERFUL_USER[u.value("name")] for u in comp.node.all("userPermissions")
                 if u.flag("enabled") and u.value("name") in POWERFUL_USER]
        named += [f"{label} on {op.value('object')}" for op in comp.node.all("objectPermissions")
                  for tag, label in POWERFUL_OBJECT.items() if op.flag(tag)]
        if not named:
            return []
        shown = ", ".join(named[:6]) + (f" and {len(named) - 6} more" if len(named) > 6 else "")
        return [Finding("access.powerful-permission", "warning", comp, f"The profile enables {shown}.",
                        "A profile deploy gives these to everyone who has the profile; keep them only if the "
                        "requirement names them, and grant the rest through permission sets.", line=comp.line,
                        subject="profile-powerful")]
    out = []
    for up in comp.node.all("userPermissions"):
        name = up.value("name")
        if up.flag("enabled") and name in POWERFUL_USER:
            out.append(Finding("access.powerful-permission", "warning", comp,
                               f"The permission set enables {POWERFUL_USER[name]}.",
                               "Keep it only if the requirement names it in writing; otherwise grant object and field "
                               "access instead.", line=up.line, subject=f"user:{name}"))
    for op in comp.node.all("objectPermissions"):
        for tag, label in POWERFUL_OBJECT.items():
            if op.flag(tag):
                obj = op.value("object")
                out.append(Finding("access.powerful-permission", "warning", comp,
                                   f"The permission set grants {label} on {obj}.",
                                   "Keep it only if the requirement names it; record sharing is bypassed for everyone "
                                   "who has this permission set.", line=op.line, subject=f"object:{obj}:{tag}"))
    return out


def _granting(comp: Component) -> dict[tuple[str, str], str]:
    """(section, key) -> label of each access entry in a permission set that grants something."""
    from .compare import ACCESS_SECTIONS, _grants
    out = {}
    if comp is None or comp.node is None:
        return out
    for e in comp.node.children:
        key_tag, _typ = ACCESS_SECTIONS.get(e.tag, ("", None))
        key = e.value(key_tag) if key_tag else ""
        if key and _grants(e):
            out[(e.tag, key.lower())] = f"{e.tag} {key}"
    return out


def permission_set_removes_grants(comp: Component, ctx: Context) -> list[Finding]:
    """A permission set deploy replaces the set: what the file does not list is removed from the org."""
    if comp.type != "PermissionSet" or comp.node is None or comp.status != "changed":
        return []
    if comp.baseline is not None:
        before, now = _granting(comp.baseline), _granting(comp)
        gone = [label for k, label in sorted(before.items()) if k not in now]
        if not gone:
            return []
        shown = ", ".join(gone[:5]) + (f" and {len(gone) - 5} more" if len(gone) > 5 else "")
        return [Finding("access.permission-set-removes-grants", "error" if len(gone) * 2 > len(before) else "warning",
                        comp, f"This deploy removes {len(gone)} of the {len(before)} grants the baseline has: {shown}. "
                        "A permission set deploy replaces the set, so what the file does not list is disabled.",
                        "If the grants should stay, add them back to the file (retrieve the set and merge your change "
                        "into it); if they should go, say so in the change record.", line=comp.line,
                        subject="removes-grants")]
    if ctx.scope_mode == "changed-from":
        return [Finding("access.permission-set-removes-grants", "note", comp,
                        "The deploy result says this permission set already exists in the org. A permission set "
                        "deploy replaces the set: any grant the org has that this file does not list is removed.",
                        "Retrieve the set from the org and compare before deploying (`torque check metadata "
                        "--baseline DIR` names what would be removed).", line=comp.line, subject="removes-grants")]
    return []


RULES = (field_without_grant, formula_field_editable, profile_in_change, powerful_permission,
         permission_set_removes_grants)
