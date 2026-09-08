"""report.py — aggregated QA report formatter."""

from __future__ import annotations

from typing import Any

from .dispatcher import DispatchResult


def format_report(
    change_types: list[dict],
    target_org: str,
    is_production: bool,
    surface_results: list[DispatchResult],
    skipped_via_token: list[tuple[str, str]] = None,
) -> str:
    """Format a human-readable aggregated QA report."""
    lines = []
    lines.append("=" * 70)
    lines.append("Torque QA — Verification Report")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"Target org:        {target_org} ({'PRODUCTION' if is_production else 'sandbox'})")
    lines.append(f"Matched change-types: {len(change_types)}")
    for ct in change_types:
        lines.append(f"  - {ct['id']}: {ct['name']}")
    lines.append("")

    # Summary counts
    counts = {"PASS": 0, "FAIL": 0, "MANUAL_REQUIRED": 0, "DEFERRED": 0,
              "SKIP_VIA_TOKEN": 0, "ERROR": 0}
    for r in surface_results:
        counts[r.status] = counts.get(r.status, 0) + 1
    lines.append(f"Summary: {counts['PASS']} PASS / {counts['FAIL']} FAIL / "
                 f"{counts['MANUAL_REQUIRED']} MANUAL_REQUIRED / "
                 f"{counts['DEFERRED']} DEFERRED / "
                 f"{counts['SKIP_VIA_TOKEN']} SKIP_VIA_TOKEN / "
                 f"{counts['ERROR']} ERROR")
    lines.append("")

    # Per-surface results
    for r in surface_results:
        icon = {
            "PASS": "✓",
            "FAIL": "✗",
            "MANUAL_REQUIRED": "⚠",
            "DEFERRED": "⏳",
            "SKIP_VIA_TOKEN": "⊘",
            "ERROR": "‼",
        }.get(r.status, "?")
        lines.append(f"{icon} {r.status:18} {r.surface:12} ({r.duration_seconds:.1f}s)")
        for detail_line in r.detail.split("\n"):
            lines.append(f"      {detail_line}")
        if r.invocation_command and r.invocation_command not in ("(automatic on Bash + MCP)",):
            lines.append(f"      → {r.invocation_command}")
        lines.append("")

    # Skipped via token
    if skipped_via_token:
        lines.append("Skipped via JSC_QA_SKIP_TOKEN:")
        for surface, reason in skipped_via_token:
            lines.append(f"  ⊘ {surface}: {reason}")
        lines.append("")

    # A missing, deferred or skipped surface is not passing coverage.
    verdict = result_exit_code(surface_results, bool(skipped_via_token))
    if verdict == 1:
        lines.append("VERDICT: FAILURES detected. Review the failed observations above.")
    elif verdict == 3:
        lines.append("VERDICT: INCOMPLETE. Some requested checks did not produce passing evidence.")
        if not surface_results:
            lines.append("No verification surfaces executed.")
    else:
        lines.append("VERDICT: All requested verification surfaces PASSED.")
    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


def result_exit_code(results: list[DispatchResult], skipped: bool = False) -> int:
    """0 complete pass; 1 observed failure/error; 3 incomplete coverage."""
    if any(result.status in ("FAIL", "ERROR") for result in results):
        return 1
    if not results or skipped or any(result.status != "PASS" for result in results):
        return 3
    return 0
