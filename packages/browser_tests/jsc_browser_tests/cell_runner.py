"""cell_runner.py — drive ONE (flow, variation, profile) cell through its lifecycle.

Lifecycle: provision → run → verify_side_effects → teardown. Crash-contained:
a failure fails the cell but teardown ALWAYS runs. No browser/org assumptions
here — the ctx carries page + sf, so this is fully offline-testable with stubs.
"""
from __future__ import annotations

import time

from .runner import FlowResult
from .preconditions import evaluate as _evaluate_precondition, UnknownToken
from .diagnostics import exception_detail, redact


def steps_status(steps) -> tuple[str, str | None]:
    """A browser cell needs observed browser steps; diagnostics cannot stand in."""
    if not steps:
        return "INCOMPLETE", "No browser steps were returned"
    if any(s.status not in {"PASS", "FAIL", "SKIP", "NOT_APPLICABLE", "WARN"} for s in steps):
        return "FAIL", "A browser step returned an unknown status"
    if any(s.status == "FAIL" for s in steps):
        return "FAIL", None
    observed = [s for s in steps if s.status in {"PASS", "WARN"}
                and s.fidelity in {"USER_FIDELITY", "VF_ACTION_FUNCTION", "LAYER_2_FALLBACK"}]
    if all(s.status in {"SKIP", "NOT_APPLICABLE"} for s in steps):
        return "SKIP", "All browser steps were skipped or not applicable"
    if not observed or any(s.fidelity == "BACKEND_DIAGNOSTIC" for s in steps):
        return "INCOMPLETE", "Backend diagnostics do not establish browser coverage"
    if any(s.status == "SKIP" for s in steps):
        return "INCOMPLETE", "Some browser steps were skipped"
    return ("WARN" if any(s.status == "WARN" for s in steps) else "PASS"), None


async def run_cell(flow, ctx) -> FlowResult:
    result = FlowResult(
        flow_name=getattr(flow, "name", None) or flow.spec.name,
        profile=ctx.profile,
        target_org=ctx.target_org,
        overall_status="INCOMPLETE",
    )
    t0 = time.monotonic()
    # Precondition gate: honest-SKIP when a required capability is absent (e.g. the
    # Pro Bono Portal needs Experience Cloud). Evaluated BEFORE provision so nothing
    # is created. An unknown token is a configuration error → FAIL.
    for _token in getattr(flow.spec, "requires", None) or []:
        try:
            _ok, _reason = _evaluate_precondition(_token, ctx.sf)
        except UnknownToken:
            result.overall_status = "FAIL"
            result.error = f"unknown precondition token: {_token}"
            result.duration_seconds = time.monotonic() - t0
            return result
        if not _ok:
            result.overall_status = "SKIP"
            result.side_effects = {"skipped": _reason, "requires": _token}
            result.duration_seconds = time.monotonic() - t0
            return result
    try:
        ctx.handles = await flow.provision(ctx) or {}
        steps = await flow.run(ctx.page, ctx, ctx.variation)
        result.steps = steps or []
        result.overall_status, result.error = steps_status(result.steps)
        try:
            result.side_effects = await flow.verify_side_effects(ctx) or {}
            # verify_side_effects reports its assertions as dict values (by convention
            # "<name>_check": "PASS"|"FAIL"|"SKIP"). cell_runner previously ignored these
            # — a flow whose only assertion lived in the verify dict (e.g. accept_check,
            # rollup_check, shift_check, clone_check) would falsely report PASS whenever
            # the run() clicks succeeded but the side-effect didn't materialize. Treat any
            # "FAIL" verify value as a cell failure so the verify dict actually gates.
            verify_fails = [k for k, v in result.side_effects.items()
                            if isinstance(v, str) and v == "FAIL"]
            if verify_fails and result.overall_status != "FAIL":
                result.overall_status = "FAIL"
                if result.error is None:
                    result.error = "verify assertion(s) failed: " + ", ".join(sorted(verify_fails))
        except Exception as e:
            result.overall_status = "FAIL"
            result.side_effects = {"verify_error": exception_detail(e)}
            if result.error is None:
                result.error = "verify_side_effects raised " + exception_detail(e)
    except Exception as e:
        result.overall_status = "FAIL"
        result.error = exception_detail(e)
    finally:
        try:
            await flow.teardown(ctx)
        except Exception as e:
            # teardown failure is a loud warning, not a silent pass
            result.side_effects["teardown_error"] = exception_detail(e)
            result.overall_status = "FAIL"
    result.side_effects = redact(result.side_effects)
    for step in result.steps:
        step.detail = redact(step.detail)
        step.side_effects = redact(step.side_effects)
    result.duration_seconds = time.monotonic() - t0
    return result
