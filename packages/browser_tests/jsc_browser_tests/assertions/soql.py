"""soql.py — value-level SOQL side-effect assertions (the step.soql helper)."""
from __future__ import annotations

import time

from ..runner import StepResult


def soql_assert(sf, name: str, query: str, expect: dict, all_rows: bool = False) -> StepResult:
    """Assert the first row of `query` contains every key/value in `expect`.

    PASS only if at least one row exists AND every expected field matches.
    The full result rows are attached to StepResult.side_effects for evidence.
    """
    t0 = time.monotonic()
    rows = sf.query(query, all_rows=all_rows)
    if not rows:
        return StepResult(step_name=name, status="FAIL", fidelity="BACKEND_DIAGNOSTIC",
                          duration_seconds=time.monotonic() - t0,
                          detail=f"no rows for: {query}", side_effects={"rows": []})
    row = rows[0]
    mismatches = {k: (v, row.get(k)) for k, v in expect.items() if row.get(k) != v}
    status = "PASS" if not mismatches else "FAIL"
    detail = "all expected fields matched" if not mismatches else f"mismatches: {mismatches}"
    return StepResult(step_name=name, status=status, fidelity="BACKEND_DIAGNOSTIC",
                      duration_seconds=time.monotonic() - t0, detail=detail,
                      side_effects={"rows": rows})
