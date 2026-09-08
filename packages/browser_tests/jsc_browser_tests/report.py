"""report.py — scorer + exit codes + (later) HTML/markdown render."""
from __future__ import annotations


def score_run(cells: list) -> dict:
    """Score per spec §7.3: P1=-5, P2=-1, any P0 forces 0; NOT_APPLICABLE + SKIP excluded.

    Cell severity: happy-path FAIL = P0; declared negative/CRUD FAIL = P1; WARN = P2.
    Cells carry side_effects {is_happy, is_crud}. NOT_APPLICABLE (profile not declared)
    and SKIP (required capability absent) didn't run → excluded from the denominator.
    """
    p0 = p1 = p2 = counted = incomplete = 0
    for c in cells:
        st = c.overall_status
        if st in ("NOT_APPLICABLE", "SKIP"):
            continue
        if st == "INCOMPLETE":
            incomplete += 1
            continue
        if st not in ("PASS", "FAIL", "WARN"):
            incomplete += 1
            continue
        counted += 1
        if st == "FAIL":
            if c.side_effects.get("is_happy", True) and not c.side_effects.get("is_crud"):
                p0 += 1
            else:
                p1 += 1
        elif st == "WARN":
            p2 += 1
    if not counted:
        score = 0
    elif p0:
        score = 0
    else:
        score = max(0, 100 - 5 * p1 - 1 * p2)
    return {"score": score, "p0": p0, "p1": p1, "p2": p2, "counted": counted,
            "incomplete": incomplete,
            "coverage_status": ("PARTIAL" if incomplete else "OBSERVED") if counted else "NOT_CHECKED"}


def exit_code(score: dict, write_gate_ok: bool, teardown_leak: bool) -> int:
    """5 write scope, 4 cleanup leak/unknown, 3 P0, 2 incomplete/P1/low score, 0 pass."""
    if not write_gate_ok:
        return 5
    if teardown_leak:
        return 4
    if score.get("p0", 0) > 0:
        return 3
    if score.get("counted", 0) == 0:
        return 2
    if score.get("p1", 0) > 0 or score.get("incomplete", 0) > 0:
        return 2
    if score.get("score", 0) < 95:
        return 2
    return 0


def render_md(cells, score) -> str:
    """Markdown report: matrix grid + honest-coverage section."""
    lines = [
        "# JSC Gold-Standard Suite Report",
        "",
        f"**Score: {score['score']}/100** — "
        f"P0={score['p0']} P1={score['p1']} P2={score['p2']} counted={score['counted']} "
        f"incomplete={score.get('incomplete', 0)}",
        "",
        "| Flow | Profile | Status |",
        "|---|---|---|",
    ]
    for c in cells:
        lines.append(f"| {c.flow_name} | {c.profile} | {c.overall_status} |")
    skipped = [c for c in cells if c.overall_status == "SKIP"]
    na = [c for c in cells if c.overall_status == "NOT_APPLICABLE"]
    warned = [c for c in cells if (c.side_effects or {}).get("teardown_error")]
    lines += [
        "",
        "## Honest coverage",
        f"- SKIPPED (capability absent): {len(skipped)}",
        f"- NOT_APPLICABLE (profile not declared): {len(na)}",
        f"- teardown warnings: {len(warned)}",
    ]
    return "\n".join(lines) + "\n"


def render_html(cells, score) -> str:
    """Minimal self-contained HTML report (same data as render_md)."""
    rows = "\n".join(
        f"<tr><td>{c.flow_name}</td><td>{c.profile}</td><td>{c.overall_status}</td></tr>"
        for c in cells
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>JSC Gold-Standard Suite Report</title></head><body>"
        "<h1>JSC Gold-Standard Suite Report</h1>"
        f"<p><strong>Score: {score['score']}/100</strong> "
        f"(P0={score['p0']} P1={score['p1']} P2={score['p2']} counted={score['counted']})</p>"
        "<table border='1'><tr><th>Flow</th><th>Profile</th><th>Status</th></tr>"
        f"{rows}</table></body></html>"
    )
