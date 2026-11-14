"""The `torque check` command group."""
from __future__ import annotations

import argparse
import json
import sys

from . import deploy_result, metadata, report, rules

METADATA_HELP = """\
Read Salesforce metadata files before a deploy and report what a careful admin would fix:
missing field access, missing descriptions and help text, fragile flows, hard-coded Ids,
profile edits, managed components, secret-looking values. Reads files only: no org, no network.

Scope (a retrieved folder is mostly baseline, so say what the change is):
  --changed-from RESULT.json  the saved JSON of `sf project deploy start --dry-run --json` (or of a
                              validate or a deploy); only components it reports as Created, Changed
                              or Failed are checked. Recommended: validate, check, then deploy.
  --baseline DIR              a copy retrieved before the change; new and changed components are
                              checked, and a finding on a changed one says whether it was inherited.
  --only Type:Name            these components only (repeatable), e.g. CustomField:Account.Region__c.
  a PATH that is a file       every file and folder given; the project around them is context.
  none of these               everything under PATH; the first line of the report says so.

Code Analyzer (--analyzer): auto runs `sf code-analyzer run` on the in-scope flow, Apex and
Lightning component files with the rule selectors Recommended and flow (the flow rules for
missing fault paths and descriptions are outside its default run) and merges its findings; a
problem both report is shown once. When it is not installed the report says so and continues.
required: exit 3 when it cannot run. off: skip it.

Exit: 0 when the report completes, whatever it found; 1 when --fail-on is met by a finding that is
not inherited; 2 unusable input; 3 --analyzer required and the analyzer did not run.
A torque-check.yml next to the project may list ignored rules or paths, each with a reason.

Rules (severity, what is inspected):
"""


def _rules_text() -> str:
    return "\n".join(f"  {rid:<38} {sev:<7} {what}" for rid, (sev, what) in rules.RULES.items())


def register(subparsers) -> None:
    check = subparsers.add_parser("check", help="check metadata before a deploy, read a saved deploy result, "
                                                "or compare what a deploy left in the org")
    actions = check.add_subparsers(dest="action", required=True)
    meta = actions.add_parser("metadata", help="static check of the metadata about to be deployed (no org)",
                              description=METADATA_HELP + _rules_text(),
                              formatter_class=argparse.RawDescriptionHelpFormatter)
    meta.add_argument("paths", nargs="*", metavar="PATH", help="project folders or files (default: .)")
    scope = meta.add_mutually_exclusive_group()
    scope.add_argument("--changed-from", metavar="RESULT.json", help="saved deploy, validate or dry-run JSON result")
    scope.add_argument("--baseline", metavar="DIR", help="folder retrieved before the change")
    scope.add_argument("--only", metavar="Type:Name", action="append", help="check only this component (repeatable)")
    meta.add_argument("--analyzer", choices=("auto", "off", "required"), default="auto")
    meta.add_argument("--ignore", metavar="RULE", action="append", default=[], help="drop a rule (repeatable)")
    meta.add_argument("--fail-on", choices=("never", "error", "warning"), default="never",
                      help="exit 1 when a finding that is not inherited is at least this severe (default never)")
    meta.add_argument("--json", action="store_true")
    res = actions.add_parser(
        "result", help="one screen from a saved `sf project deploy start|validate|report --json` result",
        description="Read a saved deploy result and print its status, deploy id, check-only or real, the target "
                    "org as the file states it, component counts, each component and test failure, coverage "
                    "warnings and the next step. Tolerates a byte-order mark, UTF-16 from PowerShell, CLI warning "
                    "lines before the JSON and the CLI envelope. No org call. Exit 0 when the file was read, "
                    "whatever the deploy outcome; 2 when it could not be read.")
    res.add_argument("result", metavar="RESULT.json")
    res.add_argument("--json", action="store_true")
    from . import deployed
    deployed.register(actions)


def _metadata(parsed, out, err) -> int:
    try:
        rep = metadata.check(parsed.paths, changed_from=parsed.changed_from, baseline=parsed.baseline,
                             only=parsed.only, analyzer_mode=parsed.analyzer, ignore=parsed.ignore)
    except metadata.InputError as exc:
        err.write(f"torque check metadata: {exc}\n")
        return 2
    out.write((json.dumps(rep, indent=2, ensure_ascii=False) if parsed.json else report.metadata_text(rep)) + "\n")
    if parsed.analyzer == "required" and rep["analyzer"]["status"] not in ("ran", "not-applicable"):
        err.write(f"torque check metadata: --analyzer required: {rep['analyzer']['summary']}\n")
        return 3
    if parsed.fail_on != "never":
        levels = ("error",) if parsed.fail_on == "error" else ("error", "warning")
        if any(f["severity"] in levels and not f["inherited"] for f in rep["findings"]):
            return 1
    return 0


def result_json(r) -> dict:
    return {"schema_version": 1, "command": "torque check result", "file": r.path, "id": r.id,
            "status": r.status, "success": r.success, "done": r.done, "check_only": r.check_only,
            "target_org": r.target_org or None, "counts": r.counts(), "declared": r.declared,
            "component_detail": r.detail,
            "component_failures": [{"type": f.type, "name": f.name, "problem": f.problem,
                                    "problem_type": f.problem_type, "line": f.line, "column": f.column,
                                    "file": f.file_path} for f in r.failures],
            "test_failures": r.test_failures, "tests_run": r.tests_run, "coverage_warnings": r.coverage_warnings,
            "cli_error": r.cli_error or None, "read_notes": r.read_notes, "next_step": report.next_step(r)}


def _result(parsed, out, err) -> int:
    try:
        r = deploy_result.read(parsed.result)
    except deploy_result.ResultError as exc:
        err.write(f"torque check result: {exc}\n")
        return 2
    out.write((json.dumps(result_json(r), indent=2, ensure_ascii=False) if parsed.json else report.result_text(r))
              + "\n")
    return 0


def run(parsed, out=None, err=None) -> int:
    out, err = out or sys.stdout, err or sys.stderr
    if parsed.action == "metadata":
        return _metadata(parsed, out, err)
    if parsed.action == "result":
        return _result(parsed, out, err)
    from . import deployed
    return deployed.run(parsed, out, err)
