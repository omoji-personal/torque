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

try:  # the shared launcher finds sf where Windows installs it as a batch file
    from jsc_common.tools import run as run_tool
except ImportError:
    run_tool = subprocess.run

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

# A query that ran and found no log analyzed nothing: that is missing evidence,
# not a clean result, and it is reported with what to do next.
NO_LOGS_SCOPE = "no_logs_found"
NO_LOGS_NEXT_STEP = ("Set a debug-log trace flag for the user who runs the change, reproduce it, "
                     "then rerun with --since set to a time just before the reproduction.")


class LogFetchError(RuntimeError):
    """The ApexLog query or the log bodies could not be read. An org with no logs is not this."""


class UnreadLogs(LogFetchError):
    """Some listed logs could not be fetched; `bodies` holds the ones that were."""

    def __init__(self, bodies: list[str], unread: int, listed: int, why: str):
        super().__init__(f"{unread} of {listed} log(s) could not be fetched ({why})")
        self.bodies, self.unread = bodies, unread


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
    """Fetch the body of the single most recent ApexLog ("" when the org has none)."""
    bodies = fetch_recent_logs(target_org, since_iso=None, limit=1)
    return bodies[0] if bodies else ""


def fetch_recent_logs(target_org: str, since_iso: str | None, limit: int) -> list[str]:
    """Fetch the bodies of up to `limit` recent ApexLogs (newest first).

    If `since_iso` is provided, restrict to logs with StartTime >= since_iso.
    `limit` is hard-capped at MAX_LOOKBACK_LOGS. Returns [] only when the query
    ran and found no log. A query that fails or cannot be read raises
    LogFetchError; listed logs whose bodies cannot be fetched raise UnreadLogs,
    which carries the bodies that were.
    """
    capped = max(1, min(limit, MAX_LOOKBACK_LOGS))
    # StartTime is a SOQL datetime literal (no surrounding quotes). The caller
    # supplies an ISO-8601 instant (the hook computes now - lookback); we sanity-
    # check the shape so a malformed value can't build a broken query.
    query = "SELECT Id, StartTime FROM ApexLog"
    if since_iso:
        if not _DATETIME_LITERAL.match(since_iso):
            raise LogFetchError("--since must be an ISO-8601 instant, such as 2026-06-13T10:00:00Z")
        query += " WHERE StartTime >= " + since_iso
    query += " ORDER BY StartTime DESC LIMIT %d" % capped

    try:
        list_proc = run_tool(
            ["sf", "data", "query", "--target-org", target_org, "--use-tooling-api",
             "--query", query, "--json"],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise LogFetchError(f"the ApexLog query could not run ({type(exc).__name__})") from exc
    if list_proc.returncode != 0:
        raise LogFetchError(f"the ApexLog query failed (sf exit {list_proc.returncode})")
    try:
        records = json.loads(list_proc.stdout)["result"]["records"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise LogFetchError("the ApexLog query result could not be read") from exc
    if not isinstance(records, list):
        raise LogFetchError("the ApexLog query result could not be read")
    log_ids = [rec["Id"] for rec in records if isinstance(rec, dict) and rec.get("Id")]
    bodies: list[str] = []
    problems: list[str] = []
    for position, log_id in enumerate(log_ids):
        try:
            body_proc = run_tool(
                ["sf", "apex", "log", "get", "--target-org", target_org, "--log-id", log_id],
                capture_output=True, text=True, timeout=60,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            # Stop: every further fetch could take its full timeout as well.
            problems.append(type(exc).__name__)
            if position + 1 < len(log_ids):
                problems.append(f"{len(log_ids) - position - 1} not attempted")
            break
        if body_proc.returncode != 0:
            problems.append(f"sf exit {body_proc.returncode}")
        elif not body_proc.stdout.strip():
            problems.append("empty body")
        else:
            bodies.append(body_proc.stdout)
    if len(bodies) < len(log_ids):
        raise UnreadLogs(bodies, len(log_ids) - len(bodies), len(log_ids), ", ".join(dict.fromkeys(problems)))
    return bodies


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    # Resolve the lookback window. --since wins; --since-deploy maps to the
    # bounded default; bare --target-org keeps the single-most-recent default.
    since_iso = args.since
    if since_iso is None and args.since_deploy:
        since_iso = _default_since_iso()

    unread, problem = 0, ""
    if args.log_file:
        log_texts = [Path(args.log_file).read_text(errors="replace", encoding="utf-8")]
    elif args.target_org:
        try:
            if since_iso is not None:
                log_texts = fetch_recent_logs(args.target_org, since_iso, args.limit)
            else:
                single = fetch_latest_log(args.target_org)
                log_texts = [single] if single else []
        except UnreadLogs as exc:
            # Analyze what was read, then report the run as incomplete.
            log_texts, unread, problem = exc.bodies, exc.unread, str(exc)
        except LogFetchError as exc:
            if args.json:
                print(json.dumps({"error": f"could not fetch log: {exc}", "score": 0, "findings": []}))
            else:
                print(f"ERROR: could not fetch log(s) from target org: {exc}", file=sys.stderr)
            return 2
        if not log_texts and not unread:
            window = f" since {since_iso}" if since_iso else ""
            if args.json:
                # No score: nothing was analyzed.
                print(json.dumps({**Result(score=0).to_dict(), "score": None, "logs_analyzed": 0,
                                  "analysis_scope": NO_LOGS_SCOPE, "next_step": NO_LOGS_NEXT_STEP}, indent=2))
            else:
                print(f"jsc-loganalyzer: no debug log found in {args.target_org}{window}; nothing was analyzed.")
                print(f"  {NO_LOGS_NEXT_STEP}")
            return 0
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
        payload = {**result.to_dict(), "logs_analyzed": len(log_texts), "analysis_scope": "provided_file" if args.log_file else ("recent_available_logs" if since_iso else "latest_available_log")}
        if unread:
            payload.update(logs_unread=unread, error=f"could not fetch log: {problem}")
            if not log_texts:
                payload["score"] = None  # nothing was analyzed
        print(json.dumps(payload, indent=2))
    else:
        if log_texts:
            print(f"jsc-loganalyzer health score: {score}/100")
            print(f"  P0: {sum(1 for f in findings if f.severity == 1)}")
            print(f"  P1: {sum(1 for f in findings if f.severity == 2)}")
            print(f"  P2: {sum(1 for f in findings if f.severity == 3)}")
            print()
        if findings and not args.quiet:
            for f in findings[:20]:
                # Finding.severity 1/2/3 displays as P0/P1/P2, as in the counts above.
                print(f"  [P{f.severity - 1}] {f.category}: {f.message}")
                if f.context:
                    print(f"    context: {f.context[:120]}")
            if len(findings) > 20:
                print(f"  ... and {len(findings) - 20} more")
        if unread:
            print(f"ERROR: could not fetch log(s) from target org: {problem}", file=sys.stderr)

    # Logs that were selected but not read leave the analysis incomplete.
    return 2 if unread else 0


if __name__ == "__main__":
    sys.exit(main())
