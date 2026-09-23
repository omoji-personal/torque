"""
Salesforce Code Analyzer v5 wrapper — static-analysis → JSC Finding/score shape.

Task B2(a). NOT a slash command (R3-7: must not break A7's command-count==40
assertion). Invokable as a module:

    python3 -m jsc_loganalyzer.code_analyzer --workspace <path>
    python3 -m jsc_loganalyzer.code_analyzer --json-input <captured.json>   # offline

It shells `sf code-analyzer run` (static analysis — NO org needed), reads the v5
JSON, maps each violation's 1-5 severity into JSC's P0/P1/P2 Finding shape
(reusing jsc_loganalyzer.score.Finding), prints a 0-100 health-score report plus
a findings summary, and exits NON-ZERO when any P0/P1 finding is present.

Severity mapping (documented; mirrors the loganalyzer convention where
Finding.severity is 1=P0, 2=P1, 3=P2):

    Code Analyzer sev 1  -> JSC severity 1  (P0)   "Critical"
    Code Analyzer sev 2  -> JSC severity 2  (P1)   "High"
    Code Analyzer sev 3  -> JSC severity 3  (P2)   "Moderate"
    Code Analyzer sev 4  -> JSC severity 3  (P2)   "Low"
    Code Analyzer sev 5  -> JSC severity 3  (P2)   "Info"

Rationale: Code Analyzer's 5-level scale is collapsed to JSC's 3-level scale.
sev1/sev2 are the only levels that should block a ship (they map to the
score-impacting P0/P1 deductions), so sev3-5 all fold into the lowest JSC
band (P2). The CI exit-code gate fires on P0/P1 only — consistent with the
JSC ship-gate discipline (0 P0 / 0 P1).

Adopted 2026-06-13 (Phase B2(a)).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from jsc_loganalyzer.score import Finding, Result, score_findings

# Subprocess timeout for the `sf code-analyzer run` invocation. Mandatory per
# verify-harness-patterns.md "Subprocess timeouts (mandatory)". Static analysis
# of a large workspace can be slow, so this is generous (10 min) but still
# bounded so a hung CLI can't hang the harness/CI.
ANALYZER_TIMEOUT_SECONDS = 600

# Code Analyzer sev (1-5) -> JSC Finding.severity (1=P0, 2=P1, 3=P2). See the
# module docstring for the rationale.
_SEVERITY_MAP = {1: 1, 2: 2, 3: 3, 4: 3, 5: 3}


def map_severity(ca_severity: int) -> int:
    """Map a Code Analyzer 1-5 severity to a JSC Finding severity (1/2/3).

    Unknown / out-of-range severities fall back to the lowest band (P2) so a
    future Code Analyzer scale change can never silently escalate to a blocking
    finding.
    """
    return _SEVERITY_MAP.get(ca_severity, 3)


def _primary_location(violation: dict) -> dict:
    """Return the violation's primary location dict (or {} if absent)."""
    locations = violation.get("locations") or []
    if not locations:
        return {}
    idx = violation.get("primaryLocationIndex", 0)
    if not isinstance(idx, int) or idx < 0 or idx >= len(locations):
        idx = 0
    loc = locations[idx]
    return loc if isinstance(loc, dict) else {}


def violations_to_findings(report: dict) -> list[Finding]:
    """Convert a parsed v5 Code Analyzer report dict into JSC Findings.

    Each `violations[]` entry (fields: engine, rule, severity, locations[],
    message) becomes one Finding whose `category` encodes the engine+rule and
    whose `context` carries the file:line location.
    """
    findings: list[Finding] = []
    for v in report.get("violations") or []:
        if not isinstance(v, dict):
            continue
        ca_sev = v.get("severity")
        ca_sev = ca_sev if isinstance(ca_sev, int) else 5
        engine = v.get("engine", "unknown")
        rule = v.get("rule", "unknown")
        message = v.get("message", "")
        loc = _primary_location(v)
        file_path = loc.get("file", "")
        start_line = loc.get("startLine", 0)
        line = start_line if isinstance(start_line, int) else 0
        context = f"{file_path}:{line}" if file_path else ""
        findings.append(
            Finding(
                category=f"{engine}:{rule}",
                severity=map_severity(ca_sev),
                message=message,
                line=line,
                context=context,
            )
        )
    return findings


def run_code_analyzer(workspace: str | None, output_file: str) -> dict:
    """Shell `sf code-analyzer run` and return the parsed v5 JSON report.

    Static analysis: NO target org. Honors a bounded subprocess timeout per
    verify-harness-patterns.md. Raises RuntimeError on failure to invoke or
    parse so the caller can surface an actionable message.
    """
    cmd = [
        "sf", "code-analyzer", "run",
        "--output-file", output_file,
        "--view", "detail",
    ]
    if workspace:
        cmd += ["--workspace", workspace]
    # R4-4: remove any pre-existing report at this path BEFORE the run, so a STALE
    # file from a prior run can never be parsed as if it were this run's output.
    out_path = Path(output_file)
    try:
        out_path.unlink()
    except FileNotFoundError:
        pass
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=ANALYZER_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"sf code-analyzer run exceeded {ANALYZER_TIMEOUT_SECONDS}s timeout; "
            f"narrow the --workspace to a smaller dir/file and retry."
        )
    except FileNotFoundError:
        raise RuntimeError(
            "sf CLI not found on PATH; install @salesforce/cli + the "
            "code-analyzer plugin (sf plugins install code-analyzer)."
        )
    # `sf code-analyzer run` exits non-zero ONLY when --severity-threshold is met
    # (we don't pass it), so a non-zero return code is not by itself failure — the
    # authoritative signal is a NON-EMPTY report written by THIS run (the path was
    # unlinked above, so existence+size>0 ⇒ this run produced it). R4-4.
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError(
            f"sf code-analyzer run did not write a report to {output_file} "
            f"(exit {proc.returncode}). stderr: {(proc.stderr or '').strip()[-400:]}"
        )
    try:
        return json.loads(out_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"could not parse Code Analyzer JSON at {output_file}: {exc}")


def load_report(json_input: str) -> dict:
    """Load a captured v5 Code Analyzer JSON report from a file (offline path)."""
    return json.loads(Path(json_input).read_text(encoding="utf-8"))


def render_report(findings: list[Finding], report: dict) -> str:
    """Render a 0-100-style health-score report + findings summary as text."""
    score = score_findings(findings)
    p0 = sum(1 for f in findings if f.severity == 1)
    p1 = sum(1 for f in findings if f.severity == 2)
    p2 = sum(1 for f in findings if f.severity == 3)
    counts = report.get("violationCounts") or {}
    versions = report.get("versions") or {}

    lines: list[str] = []
    lines.append(f"jsc code-analyzer health score: {score}/100")
    ca_ver = versions.get("code-analyzer", "?")
    lines.append(f"  (Code Analyzer engine bundle: code-analyzer {ca_ver})")
    lines.append(f"  P0 (Critical): {p0}")
    lines.append(f"  P1 (High):     {p1}")
    lines.append(f"  P2 (Moderate/Low/Info): {p2}")
    if counts:
        lines.append(
            "  raw sev counts: "
            + ", ".join(
                f"sev{n}={counts.get('sev%d' % n, 0)}" for n in range(1, 6)
            )
            + f" (total {counts.get('total', len(findings))})"
        )
    lines.append("")
    if findings:
        # P0/P1 first, then P2, preserving input order within each band.
        ordered = sorted(findings, key=lambda f: f.severity)
        shown = ordered[:30]
        for f in shown:
            # JSC Finding.severity 1/2/3 displays as P0/P1/P2.
            plabel = f"P{f.severity - 1}"
            lines.append(f"  [{plabel}] {f.category}: {f.message}")
            if f.context:
                lines.append(f"    at: {f.context}")
        if len(ordered) > len(shown):
            lines.append(f"  ... and {len(ordered) - len(shown)} more")
    else:
        lines.append("  No violations found. Clean.")
    return "\n".join(lines)


def has_blocking(findings: list[Finding]) -> bool:
    """True iff any P0 (sev 1) or P1 (sev 2) finding is present."""
    return any(f.severity in (1, 2) for f in findings)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Wrapper around Salesforce Code Analyzer v5: shells "
                    "`sf code-analyzer run`, maps violations into Torque "
                    "Finding/score shape, exits non-zero on P0/P1.",
    )
    parser.add_argument(
        "--workspace",
        help="Path passed to `sf code-analyzer run --workspace` (a dir or file "
             "subset of the codebase to analyze). Defaults to the analyzer's "
             "own default (current dir) when omitted.",
    )
    parser.add_argument(
        "--output-file",
        help="Where the analyzer writes its JSON (default: a temp file). "
             "Ignored when --json-input is used.",
    )
    parser.add_argument(
        "--json-input",
        "--from-json",
        dest="json_input",
        metavar="FILE",
        help="Parse a CAPTURED Code Analyzer v5 JSON file OFFLINE without "
             "invoking sf (used by the unit test / replay).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON (the JSC Result shape) instead of text.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if args.json_input:
        try:
            report = load_report(args.json_input)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"ERROR: could not read --json-input: {exc}", file=sys.stderr)
            return 2
    else:
        # Live path: shell sf code-analyzer. Use a temp file unless caller
        # specified --output-file.
        if args.output_file:
            output_file = args.output_file
            tmp = None
        else:
            import tempfile

            tmp = tempfile.NamedTemporaryFile(
                prefix="jsc-code-analyzer-", suffix=".json", delete=False
            )
            tmp.close()
            output_file = tmp.name
        try:
            report = run_code_analyzer(args.workspace, output_file)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        finally:
            if tmp is not None:
                try:
                    Path(output_file).unlink()
                except OSError:
                    pass

    findings = violations_to_findings(report)
    score = score_findings(findings)

    if args.json:
        result = Result(score=score, findings=findings)
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(render_report(findings, report))

    # Exit non-zero when any P0/P1 finding is present; 0 otherwise.
    return 1 if has_blocking(findings) else 0


if __name__ == "__main__":
    sys.exit(main())
