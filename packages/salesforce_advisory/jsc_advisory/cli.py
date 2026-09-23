"""CLI for JSC's model-neutral Salesforce advisory layer."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .catalogue import closure_report, entries, platform_notes, provenance
from .evidence import build_evidence, render_evidence
from .flow import render_flow, verify_flow
from .impact import (
    DEFAULT_RELATIONSHIP_QUERY_BUDGET,
    ImpactRequest,
    build_impact,
    render_impact,
)
from .receipt import build_receipt, render_receipt
from .sf import AdvisorySafetyError, SfClient, Unknown


def _emit(payload: dict | list, human: str, as_json: bool) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True) if as_json else human)


def _advisory_exit(strict: bool, complete: bool) -> int:
    """Default informational contract; strictness exists only by explicit request."""
    return 3 if strict and not complete else 0


def _add_strict_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="emit stable machine-readable JSON")
    parser.add_argument(
        "--strict", action="store_true",
        help=("explicit opt-in: return exit 3 when the report is incomplete or a "
              "verifier's positive outcome is absent; default always informs"),
    )


def _add_evidence_args(parser: argparse.ArgumentParser, *, field_required: bool) -> None:
    parser.add_argument("--field", required=field_required, metavar="Object.Field__c")
    parser.add_argument("--permset")
    parser.add_argument("--profile")
    parser.add_argument("--user-id", help="Scope permission-set assignments to this user; does not prove effective access")
    parser.add_argument("--browser-evidence")
    parser.add_argument("--automation-evidence")
    parser.add_argument("--uat-evidence")
    parser.add_argument("--na", action="append", default=[], metavar="LAYER:REASON")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only Salesforce knowledge, impact, and evidence. No subcommand authorizes, "
            "denies, or executes a change."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    notes = sub.add_parser("notes", help="rank operational notes for a command")
    notes.add_argument("--command", required=True)
    notes.add_argument("--limit", type=int, default=2)
    notes.add_argument("--json", action="store_true")

    needs = sub.add_parser("needs", help="show known direct requirements for a command")
    needs.add_argument("--command", required=True)
    needs.add_argument("--json", action="store_true")

    catalogue = sub.add_parser("catalogue", help="describe the bundled catalogue and provenance")
    catalogue.add_argument("--json", action="store_true")

    flow = sub.add_parser("flow", help="verify Flow activation through both correct APIs")
    flow.add_argument("--target-org", required=True)
    flow.add_argument("--api-name", required=True)
    _add_strict_json(flow)

    impact = sub.add_parser("impact", help="preview known impact without executing anything")
    impact.add_argument("--target-org", required=True)
    impact.add_argument("--sobject", required=True)
    impact.add_argument("--operation", default="update", choices=("insert", "update", "delete"))
    impact.add_argument("--where", default="")
    impact.add_argument(
        "--relationship-query-budget", type=int,
        default=DEFAULT_RELATIONSHIP_QUERY_BUDGET, metavar="N",
        help=("delete preview relationship-query cap; 0 requests exhaustive reads "
              f"(default: {DEFAULT_RELATIONSHIP_QUERY_BUDGET})"),
    )
    _add_strict_json(impact)

    evidence = sub.add_parser("evidence", help="assemble a nonblocking outcome ledger")
    evidence.add_argument("--target-org", required=True)
    _add_evidence_args(evidence, field_required=True)
    _add_strict_json(evidence)

    receipt = sub.add_parser("receipt", help="compose knowledge, impact, and evidence")
    receipt.add_argument("--target-org", required=True)
    receipt.add_argument("--sobject", required=True)
    receipt.add_argument("--operation", default="update", choices=("insert", "update", "delete"))
    receipt.add_argument("--where", default="")
    receipt.add_argument(
        "--relationship-query-budget", type=int,
        default=DEFAULT_RELATIONSHIP_QUERY_BUDGET, metavar="N",
        help=("delete preview relationship-query cap; 0 requests exhaustive reads "
              f"(default: {DEFAULT_RELATIONSHIP_QUERY_BUDGET})"),
    )
    _add_evidence_args(receipt, field_required=False)
    _add_strict_json(receipt)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.subcommand == "notes":
            found = platform_notes(args.command, args.limit)
            human = "\n\n".join(
                f"[{item['id']}] {item['title']} ({item['confidence']})\n"
                f"  symptom: {item['symptom']}\n  remedy : {item['remedy']}"
                for item in found
            ) or "No catalogue note matched. That is a knowledge gap, not proof there is no risk."
            _emit(found, human, args.json)
            return 0

        if args.subcommand == "needs":
            report = closure_report(args.command)
            if report["requirements"]:
                human = "\n".join(
                    f"[{item['entry']}] {item['requires']}" for item in report["requirements"]
                )
            elif report["matched"]:
                human = (
                    f"{len(report['matched'])} catalogue entries matched, but none records a "
                    "direct requirement. Treat this as a catalogue gap."
                )
            else:
                human = "No catalogue entry matched. That is not a finding that nothing is required."
            _emit(report, human, args.json)
            return 0

        if args.subcommand == "catalogue":
            payload = {"entries": len(entries()), "provenance": provenance(), "advisory": True}
            _emit(payload, f"{payload['entries']} entries; source: {payload['provenance']}", args.json)
            return 0

        client = SfClient()
        if args.subcommand == "flow":
            report = verify_flow(client, args.target_org, args.api_name)
            _emit(report, render_flow(report), args.json)
            return _advisory_exit(args.strict, report["complete"] and report["status"] == "ACTIVE")

        if args.subcommand == "impact":
            report = build_impact(
                client,
                ImpactRequest(
                    args.target_org, args.sobject, args.operation, args.where,
                    args.relationship_query_budget,
                ),
            )
            _emit(report, render_impact(report), args.json)
            return _advisory_exit(args.strict, report["complete_within_covered_surfaces"])

        common = dict(
            target_org=args.target_org,
            field_name=args.field,
            permset=args.permset,
            profile=args.profile,
            user_id=args.user_id,
            browser_evidence=args.browser_evidence,
            automation_evidence=args.automation_evidence,
            uat_evidence=args.uat_evidence,
            not_applicable=args.na,
        )
        if args.subcommand == "evidence":
            report = build_evidence(client, **common)
            _emit(report, render_evidence(report), args.json)
            return _advisory_exit(args.strict, report["complete"])

        if args.subcommand == "receipt":
            report = build_receipt(
                client,
                target_org=args.target_org,
                sobject=args.sobject,
                operation=args.operation,
                where=args.where,
                relationship_query_budget=args.relationship_query_budget,
                field_name=args.field,
                permset=args.permset,
                profile=args.profile,
            user_id=args.user_id,
                browser_evidence=args.browser_evidence,
                automation_evidence=args.automation_evidence,
                uat_evidence=args.uat_evidence,
                not_applicable=args.na,
            )
            _emit(report, render_receipt(report), args.json)
            return _advisory_exit(args.strict, report["complete"])
    except (ValueError, AdvisorySafetyError) as exc:
        print(f"jsc-advisory refused invalid input: {exc}", file=sys.stderr)
        return 2
    except Unknown as exc:
        # A top-level Unknown is still data, not permission. Default advisory mode
        # returns 0 so an unavailable source cannot interrupt another agent's work.
        payload = {"advisory": True, "complete": False, "undetermined": [str(exc)]}
        _emit(payload, f"UNDETERMINED: {exc}", getattr(args, "json", False))
        return _advisory_exit(getattr(args, "strict", False), False)
    return 2


if __name__ == "__main__":
    sys.exit(main())
