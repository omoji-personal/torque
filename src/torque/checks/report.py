"""Plain-text rendering of the check reports. The JSON report is the source; text says the same."""
from __future__ import annotations

import os

ALTERNATIVES = ("--changed-from RESULT.json (recommended: the components a dry-run reports as Created or Changed), "
                "--baseline DIR or --only Type:Name")


def scope_line(report: dict) -> str:
    s = report["scope"]
    n, by = s["in_scope"], s["by_status"]
    counts = ", ".join(f"{v} {k}" for k, v in by.items()) or "none"
    where = ", ".join(s["paths"])
    if s["mode"] == "all":
        return (f"Scope: everything under {where}: {n} component(s) in {s['files_read']} file(s). No change scope was "
                f"given, so whatever was retrieved is checked as if it were the change. To check only the change, "
                f"use {ALTERNATIVES}.")
    if s["mode"] == "changed-from":
        dr = s["deploy_result"] or {}
        kind = "check-only" if dr.get("check_only") else "deploy"
        return (f"Scope: {n} component(s) the {kind} result {s['source']} reports as Created, Changed or Failed "
                f"({counts}); {s['unchanged_not_checked']} unchanged not checked; {s['outside_the_change']} other "
                f"component(s) under {where} are not in the result.")
    if s["mode"] == "baseline":
        return (f"Scope: {n} component(s) new or changed against the baseline {s['source']} ({counts}); "
                f"{s['unchanged_not_checked']} unchanged not checked.")
    if s["mode"] == "only":
        return (f"Scope: {n} component(s) named with --only; {s['outside_the_change']} other component(s) under "
                f"{where} were read for context only.")
    return (f"Scope: {n} component(s) in the files given; {s['outside_the_change']} component(s) around them were read "
            "for context only.")


def _label(f: dict, mode: str) -> str:
    if f["inherited"] is True:
        return " [inherited]"
    if f["inherited"] is False and mode == "baseline" and f["component"]["status"] == "changed":
        return " [new in this change]"
    return ""


def metadata_text(report: dict) -> str:
    lines = [scope_line(report)]
    s = report["scope"]
    for item in s["not_found"]:
        state = f" ({item['state']})" if item.get("state") else ""
        lines.append(f"Not found under the checked folder: {item['type']}:{item['name']}{state}")
    if s["ignored_by_config"]:
        lines.append(f"{s['ignored_by_config']} component(s) skipped by {report['ignored']['config']}.")
    current = None
    for f in report["findings"]:
        c = f["component"]
        group = (c["type"], c["name"])
        if group != current:
            current = group
            lines.append("")
            status = f" ({c['status']})" if c["status"] != "unknown" else ""
            lines.append(f"{c['type']} {c['name']}{status}  {f['file']}")
        at = f":{f['line']}" if f["line"] else ""
        confirmed = f" (also {', '.join(f['confirmed_by'])})" if f["confirmed_by"] else ""
        lines.append(f"  {f['severity']:<7} {f['rule']}{at}{_label(f, s['mode'])}{confirmed}")
        lines.append(f"          {f['message']}")
        lines.append(f"          Do: {f['action']}")
    if report.get("lessons"):
        lines += ["", "From earlier work (lessons):", report["lessons"]]
    lines += ["", report["analyzer"]["summary"]]
    t = report["totals"]
    inherited = f", {t['inherited']} inherited" if t["inherited"] else ""
    if not report["findings"]:
        verdict = "No findings in scope."
    else:
        verdict = (f"Totals: {t['error']} error(s), {t['warning']} warning(s), {t['note']} note(s) in "
                   f"{t['components']} component(s){inherited}.")
    lines.append(f"{verdict} Not checked: {'; '.join(report['not_checked'])}.")
    return "\n".join(lines)


def result_text(r) -> str:
    """One screen for a saved deploy result (`torque check result`)."""
    c = r.counts()
    kind = "check-only (validate or dry-run)" if r.check_only else ("real deploy" if r.check_only is False
                                                                    else "check-only or real: not stated")
    lines = [f"Deploy {r.id or '(no id in the file)'}: {r.status or 'status not stated'}, {kind}.",
             f"Target org: {r.target_org or 'not stated in the file'}.",
             "Components: " + ", ".join(f"{v} {k}" for k, v in c.items()) + "."]
    if r.read_notes:
        lines.append("Read despite: " + "; ".join(r.read_notes) + ".")
    if r.cli_error:
        lines.append(f"CLI said: {r.cli_error}")
    if r.failures:
        lines += ["", "Component failures:"]
        for f in r.failures:
            at = f" line {f.line}" + (f":{f.column}" if f.column else "") if f.line else ""
            where = f" ({os.path.basename(f.file_path)})" if f.file_path else ""
            lines.append(f"  {f.type} {f.name}{where}{at}: {f.problem or 'no problem text'}")
    if r.test_failures:
        lines += ["", f"Test failures ({len(r.test_failures)} of {r.tests_run if r.tests_run is not None else '?'} run):"]
        for t in r.test_failures:
            lines.append(f"  {t['class']}.{t['method']}: {t['message']}")
    if r.coverage_warnings:
        lines += ["", "Coverage warnings:"]
        for w in r.coverage_warnings:
            lines.append(f"  {(w['name'] + ': ') if w['name'] else ''}{w['message']}")
    lines += ["", "Next: " + next_step(r)]
    return "\n".join(lines)


def next_step(r) -> str:
    if r.done is False or r.status in ("Queued", "Pending", "InProgress", "Canceling"):
        return (f"the deploy has not finished; save its result again with `sf project deploy report --job-id "
                f"{r.id or 'ID'} --target-org ALIAS --json` to a file, then rerun `torque check result` on it.")
    failed = r.success is False or r.status in ("Failed", "SucceededPartial", "Canceled") or r.failures \
        or r.test_failures
    if failed and r.check_only:
        return "fix the failures above and validate again (`sf project deploy start --dry-run --json` to a file)."
    if failed:
        return ("fix the failures above, validate again with a dry-run, then deploy; nothing was deployed when the "
                "deploy rolled back." if r.status != "SucceededPartial" else
                "some components deployed and some did not; fix the failures and deploy them again.")
    if r.check_only:
        return (f"`torque check metadata --changed-from {r.path}` to check what would change, then deploy "
                "(`sf project deploy quick --job-id ID` reuses this validation where the org allows it).")
    return ("confirm what landed in the org: `torque check deployed --changed-from "
            f"{r.path} --target-org ALIAS`.")
