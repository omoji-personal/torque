"""Model-neutral operation assessment composed from advisory components."""

from __future__ import annotations

from .catalogue import closure_report
from .evidence import build_evidence
from .impact import DEFAULT_RELATIONSHIP_QUERY_BUDGET, ImpactRequest, build_impact
from .sf import SfClient

OBSERVED = "OBSERVED"
PARTIAL = "PARTIAL"
NOT_CHECKED = "NOT_CHECKED"
NA = "N/A"
SETTLED = {OBSERVED, NA}


def _element(key: str, title: str, state: str, detail: str, data=None) -> dict:
    return {"element": key, "title": title, "state": state, "detail": detail,
            "data": data if data is not None else {}}


def build_receipt(client: SfClient, *, target_org: str, sobject: str,
                  operation: str = "update", where: str = "",
                  relationship_query_budget: int = DEFAULT_RELATIONSHIP_QUERY_BUDGET,
                  field_name: str | None = None, permset: str | None = None,
                  profile: str | None = None, browser_evidence: str | None = None,
                  user_id: str | None = None,
                  automation_evidence: str | None = None,
                  uat_evidence: str | None = None,
                  not_applicable: list[str] | None = None) -> dict:
    command = f"sf data {operation} bulk --sobject {sobject} --target-org {target_org}"
    if where:
        command += f" --where {where!r}"
    closure = closure_report(command)
    if closure["requirements"]:
        pre = _element(
            "preconditions", "known direct requirements", OBSERVED,
            f"{len(closure['requirements'])} requirement(s) matched the operation",
            closure,
        )
    elif closure["matched"]:
        pre = _element(
            "preconditions", "known direct requirements", PARTIAL,
            "catalogue entries matched, but none records a direct requirement; this is a gap",
            closure,
        )
    else:
        pre = _element(
            "preconditions", "known direct requirements", NOT_CHECKED,
            "no catalogue entry matched; that is not a finding that nothing is required",
            closure,
        )

    impact = build_impact(client, ImpactRequest(
        target_org, sobject, operation, where, relationship_query_budget,
    ))
    impact_state = OBSERVED if impact["complete_within_covered_surfaces"] else PARTIAL
    impact_el = _element(
        "predicted_impact", "known impact before execution", impact_state,
        "all covered sources answered" if impact_state == OBSERVED
        else f"{len(impact['undetermined'])} covered-source question(s) remain unknown",
        impact,
    )
    unknowns_el = _element(
        "unknowns", "what remains unknown", OBSERVED,
        f"{len(impact['undetermined'])} explicit unknown(s); "
        f"{len(impact['not_covered'])} intentionally uncovered surface(s)",
        {"undetermined": impact["undetermined"], "not_covered": impact["not_covered"]},
    )

    evidence = None
    if field_name:
        evidence = build_evidence(
            client, target_org=target_org, field_name=field_name, permset=permset,
            profile=profile, user_id=user_id, browser_evidence=browser_evidence,
            automation_evidence=automation_evidence, uat_evidence=uat_evidence,
            not_applicable=not_applicable,
        )
        post = _element(
            "postconditions", "post-change observations and assertions",
            OBSERVED if evidence["all_layers_observed"] else PARTIAL,
            f"{evidence['coverage']['settled']}/{evidence['coverage']['layers']} layers settled",
            evidence,
        )
    else:
        post = _element(
            "postconditions", "post-change observations and assertions", NOT_CHECKED,
            "no --field was supplied, so no field/FLS/assignment evidence was queried",
        )

    elements = [pre, impact_el, unknowns_el, post]
    complete = all(item["state"] in SETTLED for item in elements)
    elements.append(_element(
        "assessment_record", "a record another caller can inspect",
        OBSERVED if complete else PARTIAL,
        "all assessment elements are observed or inapplicable" if complete
        else "this receipt records partial evidence without presenting it as complete",
    ))
    return {
        "schema": "jsc.advisory.receipt/1",
        "advisory": True,
        "execution_proven": False,
        "execution_note": (
            "No element establishes that the described operation executed. The receipt reports "
            "knowledge, predicted impact, current org state, and operator assertions only."
        ),
        "target_org": target_org,
        "sobject": sobject,
        "operation": operation,
        "where": where,
        "complete": complete,
        "receipt": elements,
    }


def render_receipt(report: dict) -> str:
    lines = [
        f"OPERATION ASSESSMENT — {report['operation']} on "
        f"{report['sobject']} @ {report['target_org']}",
    ]
    width = max(len(item["title"]) for item in report["receipt"])
    for item in report["receipt"]:
        lines.append(f"  {item['title'].ljust(width)}  {item['state']}")
        lines.append(f"  {' ' * width}  {item['detail']}")
    lines.append(f"  assessment complete: {'yes' if report['complete'] else 'no'}")
    lines.append(f"  execution proven   : no — {report['execution_note']}")
    lines.append("  advisory only: this receipt has no authorization or blocking effect.")
    return "\n".join(lines)
