"""
jsc-loganalyzer CLI entry point.

Adopted 2026-05-04 from claudeblazer (Apache-2.0). TAA Phase 5 P2-3.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from jsc_loganalyzer.parsers import run_all_parsers
from jsc_loganalyzer.score import Result, score_findings

# Hard cap on how many ApexLogs a single bounded-lookback run will fetch +
# analyze. Bounds both the sf-CLI cost and the analysis time so the run stays
# within the post-deploy hook's ~120s subprocess timeout. (TAA B3.)
MAX_LOOKBACK_LOGS = 10

# Default lookback window for --since-deploy / bare --target-org's bounded path.
# A PostToolUse hook has NO pre-deploy timestamp, so "since deploy" can only be
# approximated by a bounded recent-log window; the hook supplies its own
# --since <iso>, and this is the in-CLI fallback for the back-compat alias.
DEFAULT_LOOKBACK_SECONDS = 600

# Accepted shape for a SOQL StartTime datetime literal (unquoted). Covers the
# ISO-8601 instants the post-deploy hook emits, e.g. 2026-06-13T10:00:00Z or
# 2026-06-13T10:00:00.000+0000 / +00:00.
_DATETIME_LITERAL = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})$"
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Closed-loop debug-log analyzer with 0–100 health score.",
    )
    parser.add_argument("--log-file", help="Local path to a debug log to analyze")
    parser.add_argument("--target-org", help="sf CLI alias to fetch logs from")
    parser.add_argument(
        "--since",
        metavar="ISO",
        help="Bounded recent-log analysis: fetch + analyze every ApexLog with "
             "StartTime >= <ISO> (newest first, capped at %d logs) and aggregate "
             "the findings. ISO-8601 UTC, e.g. 2026-06-13T10:00:00Z." % MAX_LOOKBACK_LOGS,
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=MAX_LOOKBACK_LOGS,
        help="Cap on logs analyzed in a --since window (hard-capped at %d)." % MAX_LOOKBACK_LOGS,
    )
    parser.add_argument(
        "--since-deploy",
        action="store_true",
        help="Back-compat alias: enable bounded recent-log analysis using the "
             "default lookback window (now - %ds). A PostToolUse hook has no "
             "pre-deploy timestamp, so this is a bounded approximation, NOT a "
             "true deploy-start capture. Equivalent to --since <now-%ds>. The "
             "post-deploy hook supplies an explicit --since instead." % (
                 DEFAULT_LOOKBACK_SECONDS, DEFAULT_LOOKBACK_SECONDS),
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON output (consumed by post-deploy hooks)")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-finding output")
    return parser.parse_args(argv)


def _default_since_iso() -> str:
    """ISO-8601 UTC timestamp for `now - DEFAULT_LOOKBACK_SECONDS` (the
    --since-deploy back-compat default)."""
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    since = now - datetime.timedelta(seconds=DEFAULT_LOOKBACK_SECONDS)
    return since.strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_latest_log(target_org: str) -> str:
    """Fetch the body of the single most recent ApexLog from the target org."""
    bodies = fetch_recent_logs(target_org, since_iso=None, limit=1)
    return bodies[0] if bodies else ""


def fetch_recent_logs(target_org: str, since_iso: str | None, limit: int) -> list[str]:
    """Fetch the bodies of up to `limit` recent ApexLogs (newest first).

    If `since_iso` is provided, restrict to logs with StartTime >= since_iso.
    `limit` is hard-capped at MAX_LOOKBACK_LOGS. Returns [] on any failure
    (subprocess/timeout/parse) — the analyzer fails soft, never raising.
    """
    capped = max(1, min(limit, MAX_LOOKBACK_LOGS))
    # StartTime is a SOQL datetime literal (no surrounding quotes). The caller
    # supplies an ISO-8601 instant (the hook computes now - lookback); we sanity-
    # check the shape so a malformed value can't build a broken query.
    query = "SELECT Id, StartTime FROM ApexLog"
    if since_iso:
        if not _DATETIME_LITERAL.match(since_iso):
            return []
        query += " WHERE StartTime >= " + since_iso
    query += " ORDER BY StartTime DESC LIMIT %d" % capped

    bodies: list[str] = []
    try:
        list_proc = subprocess.run(
            ["sf", "data", "query", "--target-org", target_org, "--use-tooling-api",
             "--query", query, "--json"],
            capture_output=True, text=True, timeout=60,
        )
        if list_proc.returncode != 0:
            return []
        list_data = json.loads(list_proc.stdout)
        records = list_data.get("result", {}).get("records", [])
        for rec in records:
            log_id = rec.get("Id")
            if not log_id:
                continue
            body_proc = subprocess.run(
                ["sf", "apex", "log", "get", "--target-org", target_org, "--log-id", log_id],
                capture_output=True, text=True, timeout=60,
            )
            if body_proc.returncode == 0 and body_proc.stdout.strip():
                bodies.append(body_proc.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        return bodies
    return bodies


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    # Resolve the lookback window. --since wins; --since-deploy maps to the
    # bounded default; bare --target-org keeps the single-most-recent default.
    since_iso = args.since
    if since_iso is None and args.since_deploy:
        since_iso = _default_since_iso()

    if args.log_file:
        log_texts = [Path(args.log_file).read_text(errors="replace")]
    elif args.target_org:
        if since_iso is not None:
            log_texts = fetch_recent_logs(args.target_org, since_iso, args.limit)
        else:
            single = fetch_latest_log(args.target_org)
            log_texts = [single] if single else []
        if not log_texts:
            if args.json:
                print(json.dumps({"error": "could not fetch log", "score": 0, "findings": []}))
            else:
                print("ERROR: could not fetch log(s) from target org", file=sys.stderr)
            return 2
    else:
        print("ERROR: provide either --log-file or --target-org", file=sys.stderr)
        return 2

    # Aggregate findings across every analyzed log.
    findings = []
    for log_text in log_texts:
        findings.extend(run_all_parsers(log_text))
    score = score_findings(findings)
    result = Result(score=score, findings=findings)

    if args.json:
        print(json.dumps({**result.to_dict(), "logs_analyzed": len(log_texts), "analysis_scope": "provided_file" if args.log_file else ("recent_available_logs" if since_iso else "latest_available_log")}, indent=2))
    else:
        print(f"jsc-loganalyzer health score: {score}/100")
        print(f"  P0: {sum(1 for f in findings if f.severity == 1)}")
        print(f"  P1: {sum(1 for f in findings if f.severity == 2)}")
        print(f"  P2: {sum(1 for f in findings if f.severity == 3)}")
        print()
        if findings and not args.quiet:
            for f in findings[:20]:
                print(f"  [P{f.severity}] {f.category}: {f.message}")
                if f.context:
                    print(f"    context: {f.context[:120]}")
            if len(findings) > 20:
                print(f"  ... and {len(findings) - 20} more")

    return 0


if __name__ == "__main__":
    sys.exit(main())
