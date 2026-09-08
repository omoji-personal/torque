"""Live Flow activation verification using the correct Salesforce APIs."""

from __future__ import annotations

from .sf import SfClient, Unknown, records, require_api_name, soql_literal


def verify_flow(client: SfClient, target_org: str, api_name: str) -> dict:
    """Check a Flow definition through both supported definition-scoped paths.

    ``FlowDefinition`` is queried through Tooling. ``FlowDefinitionView`` is
    queried through the standard API. Retrieved Flow XML is intentionally not
    used because it represents the latest version, not necessarily the active
    version.
    """
    require_api_name(api_name, "Flow API name")
    literal = soql_literal(api_name)
    sources: dict[str, dict] = {}
    unknowns: list[str] = []

    try:
        tooling_rows = records(client.query(
            target_org,
            "SELECT DeveloperName, ActiveVersionId, LatestVersionId "
            f"FROM FlowDefinition WHERE DeveloperName = '{literal}'",
            tooling=True,
        ))
        sources["tooling_flow_definition"] = {
            "api": "tooling",
            "rows": len(tooling_rows),
            "active_version_id": tooling_rows[0].get("ActiveVersionId") if tooling_rows else None,
            "latest_version_id": tooling_rows[0].get("LatestVersionId") if tooling_rows else None,
            "active": bool(tooling_rows and tooling_rows[0].get("ActiveVersionId")),
        }
    except Unknown as exc:
        sources["tooling_flow_definition"] = {"api": "tooling", "error": str(exc)}
        unknowns.append(f"Tooling FlowDefinition: {exc}")

    try:
        view_rows = records(client.query(
            target_org,
            "SELECT ApiName, IsActive, ProcessType, TriggerType "
            f"FROM FlowDefinitionView WHERE ApiName = '{literal}'",
            tooling=False,
        ))
        sources["standard_flow_definition_view"] = {
            "api": "standard",
            "rows": len(view_rows),
            "active": bool(view_rows and view_rows[0].get("IsActive")),
            "process_type": view_rows[0].get("ProcessType") if view_rows else None,
            "trigger_type": view_rows[0].get("TriggerType") if view_rows else None,
        }
    except Unknown as exc:
        sources["standard_flow_definition_view"] = {"api": "standard", "error": str(exc)}
        unknowns.append(f"standard FlowDefinitionView: {exc}")

    answered = [s for s in sources.values() if "error" not in s]
    present = [s for s in answered if s.get("rows", 0) > 0]
    absent = [s for s in answered if s.get("rows", 0) == 0]
    active_values = {bool(s.get("active")) for s in present}

    if not answered:
        status = "UNKNOWN"
    elif answered and not present:
        status = "NOT_FOUND" if len(answered) == 2 else "PARTIALLY_OBSERVED_NOT_FOUND"
    elif present and absent:
        status = "INCONSISTENT"
        unknowns.append("the definition-scoped sources disagree about whether the Flow exists")
    elif len(active_values) > 1:
        status = "INCONSISTENT"
        unknowns.append("the Tooling and standard definition-scoped sources disagree")
    elif active_values == {True}:
        status = "ACTIVE" if len(answered) == 2 else "PARTIALLY_OBSERVED_ACTIVE"
    else:
        status = "INACTIVE" if len(answered) == 2 else "PARTIALLY_OBSERVED_INACTIVE"

    complete = len(answered) == 2 and not unknowns and status not in {"INCONSISTENT", "UNKNOWN"}
    return {
        "advisory": True,
        "target_org": target_org,
        "flow_api_name": api_name,
        "status": status,
        "complete": complete,
        "sources": sources,
        "undetermined": unknowns,
        "guidance": (
            "Activation is definition-scoped. Tooling FlowDefinition.ActiveVersionId and "
            "standard FlowDefinitionView.IsActive are authoritative; latest retrieved XML is not."
        ),
    }


def render_flow(report: dict) -> str:
    lines = [
        f"FLOW ACTIVATION — {report['flow_api_name']} @ {report['target_org']}",
        f"  status      : {report['status']}",
        f"  complete    : {'yes' if report['complete'] else 'no'}",
    ]
    for name, source in report["sources"].items():
        if source.get("error"):
            lines.append(f"  {name}: UNDETERMINED — {source['error']}")
        else:
            lines.append(
                f"  {name}: rows={source.get('rows')} active={source.get('active')} "
                f"via {source.get('api')} API"
            )
    for item in report["undetermined"]:
        lines.append(f"  unknown     : {item}")
    lines.append(f"  note        : {report['guidance']}")
    return "\n".join(lines)
