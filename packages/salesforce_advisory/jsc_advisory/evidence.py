"""Advisory post-change evidence ledger.

Unlike Torque's completion gate, this ledger never withholds a word or blocks a
workflow. It records the source of each claim and leaves incompleteness visible
to any caller, human or model.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from .sf import SfClient, Unknown, records, require_api_name

OBSERVED = "OBSERVED"
ASSERTED = "ASSERTED_BY_OPERATOR"
NOT_OBSERVED = "NOT_OBSERVED"
NOT_CHECKED = "NOT_CHECKED"
UNKNOWN = "UNKNOWN"
NA = "N/A"
SETTLED = {OBSERVED, ASSERTED, NA}


@dataclass
class Layer:
    key: str
    title: str
    outcome: str
    detail: str
    source: str
    declared_not_applicable: str | None = None

    def as_dict(self) -> dict:
        return {
            "layer": self.key,
            "title": self.title,
            "outcome": self.outcome,
            "detail": self.detail,
            "source": self.source,
            "declared_not_applicable": self.declared_not_applicable,
        }


def _query(client: SfClient, target_org: str, soql: str, *, tooling: bool = False):
    try:
        return records(client.query(target_org, soql, tooling=tooling)), None
    except Unknown as exc:
        return None, str(exc)


def _field_exists(client: SfClient, target_org: str, obj: str, field: str) -> Layer:
    rows, error = _query(
        client, target_org,
        "SELECT QualifiedApiName FROM FieldDefinition WHERE "
        f"EntityDefinition.QualifiedApiName='{obj}' AND QualifiedApiName='{field}'",
        tooling=True,
    )
    if error:
        return Layer("field_exists", f"{obj}.{field} exists", UNKNOWN,
                     f"the org could not answer: {error}", "Tooling FieldDefinition")
    if not rows:
        return Layer("field_exists", f"{obj}.{field} exists", NOT_OBSERVED,
                     "the org answered and returned no matching field", "Tooling FieldDefinition")
    return Layer("field_exists", f"{obj}.{field} exists", OBSERVED,
                 "the field was found in the target org", "Tooling FieldDefinition")


def _fls(client: SfClient, target_org: str, obj: str, field: str,
         permset: str | None) -> Layer:
    if permset:
        rows, error = _query(
            client, target_org,
            "SELECT Id, PermissionsRead, PermissionsEdit FROM FieldPermissions "
            f"WHERE Field='{obj}.{field}' AND Parent.Name='{permset}'",
        )
        title = f"{permset} grants read access to {obj}.{field}"
    else:
        rows, error = _query(
            client, target_org,
            f"SELECT Id, Parent.Name, PermissionsRead, PermissionsEdit FROM FieldPermissions "
            f"WHERE Field='{obj}.{field}'",
        )
        title = f"some permission source grants read access to {obj}.{field}"
    if error:
        return Layer("fls", title, UNKNOWN, f"the org could not answer: {error}",
                     "standard FieldPermissions")
    readable = [row for row in rows or [] if row.get("PermissionsRead")]
    if readable:
        qualifier = "the named permission set" if permset else "at least one permission source"
        return Layer("fls", title, OBSERVED,
                     f"{len(readable)} readable permission row(s); {qualifier} was observed",
                     "standard FieldPermissions")
    return Layer("fls", title, NOT_OBSERVED,
                 "the org answered and no readable permission row matched",
                 "standard FieldPermissions")


def _aggregate_count(rows: list[dict] | None) -> int:
    if not rows:
        return 0
    value = rows[0].get("c", rows[0].get("expr0", 0))
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _assignment(client: SfClient, target_org: str, permset: str | None, user_id: str | None = None) -> Layer:
    if not permset:
        return Layer("assignment", "an intended user holds the permission", NOT_CHECKED,
                     "no --permset was supplied", "not queried")
    assignee = f" AND AssigneeId='{user_id}'" if user_id else ""
    direct, direct_error = _query(
        client, target_org,
        "SELECT COUNT(Id) c FROM PermissionSetAssignment "
        f"WHERE PermissionSet.Name='{permset}'" + assignee,
    )
    grouped, group_error = _query(
        client, target_org,
        "SELECT COUNT(Id) c FROM PermissionSetAssignment WHERE PermissionSetGroupId IN "
        "(SELECT PermissionSetGroupId FROM PermissionSetGroupComponent "
        f"WHERE PermissionSet.Name='{permset}')" + assignee,
    )
    title = f"{user_id or 'somebody in the org'} holds {permset}, directly or through a Permission Set Group"
    if direct_error or group_error:
        detail = "; ".join(x for x in (
            f"direct query: {direct_error}" if direct_error else "",
            f"group query: {group_error}" if group_error else "",
        ) if x)
        return Layer("assignment", title, UNKNOWN, detail,
                     "standard PermissionSetAssignment and PermissionSetGroupComponent")
    count = _aggregate_count(direct) + _aggregate_count(grouped)
    if count:
        return Layer("assignment", title, OBSERVED, f"{count} assignment(s) observed",
                     "standard PermissionSetAssignment and PermissionSetGroupComponent")
    return Layer("assignment", title, NOT_OBSERVED,
                 "the org answered and returned zero direct or group-based assignments",
                 "standard PermissionSetAssignment and PermissionSetGroupComponent")


def _asserted_layer(key: str, title: str, evidence: str | None, flag: str) -> Layer:
    if evidence:
        return Layer(key, title, ASSERTED, evidence, "operator-supplied evidence")
    return Layer(key, title, NOT_CHECKED, f"no {flag} evidence was supplied",
                 "not queried")


def build_evidence(client: SfClient, *, target_org: str, field_name: str,
                   permset: str | None = None, profile: str | None = None,
                   user_id: str | None = None,
                   browser_evidence: str | None = None,
                   automation_evidence: str | None = None,
                   uat_evidence: str | None = None,
                   not_applicable: list[str] | None = None) -> dict:
    obj, separator, field = field_name.partition(".")
    if not separator:
        raise ValueError("--field must be Object.Field__c")
    require_api_name(obj, "sObject API name")
    require_api_name(field, "field API name")
    if permset:
        require_api_name(permset, "permission set API name")

    if user_id and not re.fullmatch(r"005[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?", user_id):
        raise ValueError("--user-id must be a 15- or 18-character Salesforce User Id")

    layers = [
        _field_exists(client, target_org, obj, field),
        _fls(client, target_org, obj, field, permset),
        _assignment(client, target_org, permset, user_id),
        _asserted_layer(
            "profile_render",
            f"{field_name} rendered for {profile or 'the intended non-admin profile'}",
            browser_evidence,
            "browser",
        ),
        _asserted_layer("automation", "the expected automation fired",
                        automation_evidence, "automation"),
        _asserted_layer("human_uat", "a human used the change successfully",
                        uat_evidence, "UAT"),
    ]

    by_key = {layer.key: layer for layer in layers}
    for spec in not_applicable or []:
        key, separator, reason = spec.partition(":")
        if not separator or not reason.strip():
            raise ValueError(f"--na requires LAYER:REASON, got {spec!r}")
        if key not in by_key:
            raise ValueError(f"unknown evidence layer {key!r}; choices: {', '.join(by_key)}")
        layer = by_key[key]
        layer.declared_not_applicable = reason.strip()
        layer.detail += f"; operator declared inapplicable ({reason.strip()}); original observation retained"

    settled = [layer for layer in layers if layer.outcome in SETTLED]
    observed = [layer for layer in layers if layer.outcome == OBSERVED]
    asserted = [layer for layer in layers if layer.outcome == ASSERTED]
    complete = len(settled) == len(layers)
    return {
        "advisory": True,
        "target_org": target_org,
        "field": field_name,
        "complete": complete,
        "completeness_meaning": "Every layer has an observation or assertion; this is not proof of effective user access or execution.",
        "intended_user_id": user_id,
        "assignment_scope": "intended_user" if user_id else "any_assignee_in_org",
        "effective_user_access_proven": False,
        "all_layers_observed": len(observed) == len(layers),
        "coverage": {
            "settled": len(settled), "observed": len(observed),
            "asserted": len(asserted), "layers": len(layers),
            "operator_exceptions": sum(bool(layer.declared_not_applicable) for layer in layers),
        },
        "ledger": [layer.as_dict() for layer in layers],
        "outstanding": [layer.key for layer in layers if layer.outcome not in SETTLED],
        "note": "Incomplete evidence is reported but does not block or alter the existing workflow.",
    }


def render_evidence(report: dict) -> str:
    lines = [f"EVIDENCE LEDGER — {report['field']} @ {report['target_org']}"]
    width = max(len(layer["title"]) for layer in report["ledger"])
    for layer in report["ledger"]:
        lines.append(f"  {layer['title'].ljust(width)}  {layer['outcome']}")
        lines.append(f"  {' ' * width}  {layer['detail']} [{layer['source']}]")
    shape = report["coverage"]
    lines.append(
        f"  coverage: {shape['settled']}/{shape['layers']} settled; "
        f"{shape['observed']} org-observed; {shape['asserted']} operator-asserted"
    )
    if report["outstanding"]:
        lines.append("  outstanding: " + ", ".join(report["outstanding"]))
    lines.append("  effective user access: not proven; assignment alone does not establish usable access.")
    lines.append("  advisory only: incompleteness does not block or change command access.")
    return "\n".join(lines)
