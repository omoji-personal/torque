"""Read-only, explicitly bounded Salesforce impact preview."""

from __future__ import annotations

from dataclasses import dataclass

from .sf import SfClient, Unknown, records, require_api_name, total_size


COVERED_SURFACES = (
    "record scope", "Apex triggers", "record-triggered Flows", "validation rules",
    "workflow rules", "candidate roll-up summaries", "delete cascades",
    "lookup orphans", "restricted-delete blockers",
)
NOT_COVERED = (
    "duplicate and matching rules", "assignment and escalation rules",
    "invocable Apex reached indirectly", "sharing recalculation",
    "platform-event subscribers", "Bulk API chunking and lock order",
    "external side effects such as callouts, email, and async work",
)
_NON_QUERYABLE_VIEWS = {
    "ActivityHistory", "OpenActivity", "NoteAndAttachment", "CombinedAttachment",
    "AttachedContentDocument", "AttachedContentNote", "EmailStatus",
    "LookedUpFromActivity",
}
_NOT_A_TABLE = ("INVALID_TYPE_FOR_OPERATION", "does not support query")
DEFAULT_RELATIONSHIP_QUERY_BUDGET = 12


@dataclass
class ImpactRequest:
    target_org: str
    sobject: str
    operation: str = "update"
    where: str = ""
    relationship_query_budget: int = DEFAULT_RELATIONSHIP_QUERY_BUDGET


def _scope(client: SfClient, req: ImpactRequest) -> int:
    if req.operation == "insert":
        raise Unknown("insert scope requires the input rows; no input file was supplied")
    query = f"SELECT COUNT() FROM {req.sobject}"
    if req.where:
        query += f" WHERE {req.where}"
    return total_size(client.query(req.target_org, query))


def _triggers(client: SfClient, req: ImpactRequest) -> list[str]:
    usage = {
        "insert": ("UsageBeforeInsert", "UsageAfterInsert"),
        "update": ("UsageBeforeUpdate", "UsageAfterUpdate"),
        "delete": ("UsageBeforeDelete", "UsageAfterDelete"),
    }[req.operation]
    rows = records(client.query(
        req.target_org,
        "SELECT Name, Status, UsageBeforeInsert, UsageAfterInsert, UsageBeforeUpdate, "
        "UsageAfterUpdate, UsageBeforeDelete, UsageAfterDelete FROM ApexTrigger "
        f"WHERE TableEnumOrId = '{req.sobject}'",
        tooling=True,
    ))
    return [row["Name"] for row in rows
            if row.get("Status") == "Active" and any(row.get(field) for field in usage)]


def _flows(client: SfClient, req: ImpactRequest) -> list[str]:
    rows = records(client.query(
        req.target_org,
        "SELECT ApiName, Label, TriggerType, RecordTriggerType, TriggerOrder, IsActive "
        "FROM FlowDefinitionView "
        f"WHERE TriggerObjectOrEventId = '{req.sobject}' AND IsActive = true",
    ))
    out: list[str] = []
    for row in rows:
        trigger_type = row.get("TriggerType") or ""
        if req.operation == "delete" and "Delete" not in trigger_type:
            continue
        if req.operation in ("insert", "update") and "Delete" in trigger_type:
            continue
        record_type = row.get("RecordTriggerType") or ""
        if req.operation == "insert" and record_type not in ("Create", "CreateAndUpdate"):
            continue
        if req.operation == "update" and record_type not in ("Update", "CreateAndUpdate"):
            continue
        out.append(f"{row.get('Label') or row.get('ApiName')} [{trigger_type or 'unknown'}]")
    return out


def _validation_rules(client: SfClient, req: ImpactRequest) -> list[str]:
    if req.operation == "delete":
        return []
    rows = records(client.query(
        req.target_org,
        "SELECT ValidationName, Active FROM ValidationRule "
        f"WHERE EntityDefinition.QualifiedApiName = '{req.sobject}'",
        tooling=True,
    ))
    return [row["ValidationName"] for row in rows if row.get("Active")]


def _workflow_rules(client: SfClient, req: ImpactRequest) -> list[str]:
    if req.operation == "delete":
        return []
    rows = records(client.query(
        req.target_org,
        f"SELECT Id, Name FROM WorkflowRule WHERE TableEnumOrId = '{req.sobject}'",
        tooling=True,
    ))
    out: list[str] = []
    if len(rows) > 25:
        out.append(
            f"({len(rows) - 25} further workflow rule(s) UNDETERMINED — cap reached)"
        )
    for row in rows[:25]:
        try:
            metadata_rows = records(client.query(
                req.target_org,
                f"SELECT Metadata FROM WorkflowRule WHERE Id = '{row['Id']}'",
                tooling=True,
            ))
            metadata = metadata_rows[0].get("Metadata") if metadata_rows else None
            if isinstance(metadata, dict) and metadata.get("active"):
                out.append(row["Name"])
        except Unknown:
            out.append(f"{row['Name']} (active state UNDETERMINED)")
    return out


def _rollups(client: SfClient, req: ImpactRequest) -> list[str]:
    description = client.describe(req.target_org, req.sobject)
    parents: list[str] = []
    for field in description.get("fields") or []:
        if field.get("type") == "reference" and field.get("relationshipName"):
            for parent in field.get("referenceTo") or []:
                if parent not in parents:
                    parents.append(parent)
    out: list[str] = []
    if len(parents) > 12:
        out.append(
            f"({len(parents) - 12} further parent object(s) UNDETERMINED — cap reached)"
        )
    for parent in parents[:12]:
        try:
            parent_description = client.describe(req.target_org, parent)
        except Unknown:
            out.append(f"{parent}.* (UNDETERMINED — describe failed)")
            continue
        for field in parent_description.get("fields") or []:
            if field.get("type") == "summary":
                out.append(
                    f"{parent}.{field.get('name')} "
                    "(candidate; summarized relationship UNDETERMINED)"
                )
    return out


def _delete_relationships(client: SfClient, req: ImpactRequest) -> dict:
    if req.operation != "delete":
        return {"state": "not-applicable"}
    description = client.describe(req.target_org, req.sobject)
    cascades: list[tuple[str, str]] = []
    lookups: list[tuple[str, str]] = []
    restrictions: list[tuple[str, str]] = []
    views: list[str] = []
    for relation in description.get("childRelationships") or []:
        child, field = relation.get("childSObject"), relation.get("field")
        if not child or not field:
            continue
        if child in _NON_QUERYABLE_VIEWS:
            if child not in views:
                views.append(child)
            continue
        pair = (child, field)
        bucket = cascades if relation.get("cascadeDelete") else (
            restrictions if relation.get("restrictedDelete") else lookups
        )
        if pair not in bucket:
            bucket.append(pair)

    parent_query = f"SELECT Id FROM {req.sobject}"
    if req.where:
        parent_query += f" WHERE {req.where}"
    unknowns: list[str] = []
    queries_used = 0
    budget_exhausted = False

    def counts(pairs: list[tuple[str, str]], kind: str) -> list[dict]:
        nonlocal queries_used, budget_exhausted
        out: list[dict] = []
        for index, (child, field) in enumerate(pairs):
            if child == req.sobject:
                out.append({"sobject": child, "field": field, "count": None})
                unknowns.append(f"{kind}:{child}.{field} self-reference needs a separate count")
                continue
            if req.relationship_query_budget and queries_used >= req.relationship_query_budget:
                remaining = len(pairs) - index
                unknowns.append(
                    f"{kind}: {remaining} relationship(s) UNDETERMINED — read-query budget "
                    f"of {req.relationship_query_budget} exhausted"
                )
                budget_exhausted = True
                break
            queries_used += 1
            try:
                count = total_size(client.query(
                    req.target_org,
                    f"SELECT COUNT() FROM {child} WHERE {field} IN ({parent_query})",
                ))
                if count or kind != "lookup-orphan":
                    out.append({"sobject": child, "field": field, "count": count})
            except Unknown as exc:
                if any(marker in str(exc) for marker in _NOT_A_TABLE):
                    if child not in views:
                        views.append(child)
                    continue
                out.append({"sobject": child, "field": field, "count": None})
                unknowns.append(f"{kind}:{child}.{field} ({exc})")
        return out

    # Protect the high-consequence relationships first. Lookup-orphan discovery
    # can fan out into hundreds of CLI processes on an Account describe; it uses
    # whatever remains of the shared budget after cascades and blockers.
    cascade_rows = counts(cascades, "cascade")
    blocker_rows = counts(restrictions, "delete-blocker")
    orphan_rows = counts(lookups, "lookup-orphan")

    return {
        "state": "observed",
        "cascades": cascade_rows,
        "lookup_orphans": orphan_rows,
        "delete_blockers": blocker_rows,
        "non_queryable_views": views,
        "query_budget": {
            "limit": req.relationship_query_budget,
            "used": queries_used,
            "exhaustive_requested": req.relationship_query_budget == 0,
            "exhausted": budget_exhausted,
        },
        "undetermined": unknowns,
    }


def build_impact(client: SfClient, req: ImpactRequest) -> dict:
    require_api_name(req.sobject, "sObject API name")
    if req.operation not in {"insert", "update", "delete"}:
        raise ValueError(f"unsupported operation: {req.operation}")
    if req.relationship_query_budget < 0:
        raise ValueError("relationship query budget must be zero (exhaustive) or positive")
    report: dict = {}
    unknowns: list[str] = []

    def part(name: str, fn) -> None:
        try:
            report[name] = fn(client, req)
        except Unknown as exc:
            report[name] = None
            unknowns.append(f"{name}: {exc}")

    part("scope", _scope)
    part("triggers", _triggers)
    part("flows", _flows)
    part("validation_rules", _validation_rules)
    part("workflow_rules", _workflow_rules)
    part("rollups", _rollups)
    if req.operation == "delete" and report.get("scope") == 0:
        report["delete_relationships"] = {
            "state": "empty-scope", "cascades": [], "lookup_orphans": [],
            "delete_blockers": [], "non_queryable_views": [],
            "query_budget": {
                "limit": req.relationship_query_budget, "used": 0,
                "exhaustive_requested": req.relationship_query_budget == 0,
                "exhausted": False,
            },
            "undetermined": [],
        }
    else:
        part("delete_relationships", _delete_relationships)

    rendered_unknowns = []
    for value in report.values():
        if "UNDETERMINED" in str(value):
            rendered_unknowns.append(str(value))
    relationships = report.get("delete_relationships")
    if isinstance(relationships, dict):
        unknowns.extend(relationships.get("undetermined") or [])
    if rendered_unknowns:
        unknowns.append("one or more returned items explicitly remain UNDETERMINED")

    report.update({
        "advisory": True,
        "target_org": req.target_org,
        "sobject": req.sobject,
        "operation": req.operation,
        "where": req.where,
        "relationship_query_budget": req.relationship_query_budget,
        "complete_within_covered_surfaces": not unknowns,
        "covered_surfaces": list(COVERED_SURFACES),
        "not_covered": list(NOT_COVERED),
        "undetermined": list(dict.fromkeys(unknowns)),
    })
    return report


def render_impact(report: dict) -> str:
    lines = [
        f"IMPACT PREVIEW — {report['operation']} on {report['sobject']} @ {report['target_org']}",
        f"  criteria    : {report.get('where') or '(all records)' }",
        f"  scope       : {report.get('scope') if report.get('scope') is not None else 'UNDETERMINED'}",
    ]
    for key in ("triggers", "flows", "validation_rules", "workflow_rules", "rollups"):
        value = report.get(key)
        if value is None:
            lines.append(f"  {key:12}: UNDETERMINED")
        elif value:
            lines.append(f"  {key:12}: {len(value)}")
            lines.extend(f"                 - {item}" for item in value)
        else:
            lines.append(f"  {key:12}: none")
    rel = report.get("delete_relationships")
    if isinstance(rel, dict) and rel.get("state") == "observed":
        for key in ("cascades", "lookup_orphans", "delete_blockers"):
            rows = rel.get(key) or []
            lines.append(f"  {key:12}: {len(rows)} relationship(s) with observed/unknown counts")
            for row in rows:
                lines.append(
                    f"                 - {row['sobject']}.{row['field']}: "
                    f"{row['count'] if row['count'] is not None else 'UNDETERMINED'}"
                )
        budget = rel.get("query_budget") or {}
        limit = budget.get("limit")
        lines.append(
            f"  rel queries : {budget.get('used', 0)}/"
            f"{limit if limit else 'unlimited'}"
            + (" (budget exhausted)" if budget.get("exhausted") else "")
        )
    elif isinstance(rel, dict) and rel.get("state") == "empty-scope":
        lines.append("  relationships: not queried — record scope is zero")
    lines.append(
        "  completeness: " +
        ("complete within covered surfaces" if report["complete_within_covered_surfaces"]
         else "INCOMPLETE within covered surfaces")
    )
    for item in report["undetermined"]:
        lines.append(f"  unknown     : {item}")
    lines.append("  not covered : " + "; ".join(report["not_covered"]))
    lines.append("  advisory only: this result never authorizes, denies, or executes the operation.")
    return "\n".join(lines)
