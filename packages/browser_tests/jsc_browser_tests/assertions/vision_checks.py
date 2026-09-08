"""vision_checks.py — map vision.py statuses to cell dispositions (spec §6).

Pure policy layer: takes a vision status string and returns a StepResult. The
orchestrator (suite) calls vision.analyze_screenshot() then passes its .status
here — keeping this module free of vision.py's subprocess/gemini deps.

Dispositions:
  GEMINI_UNAVAILABLE / MODELS_EXHAUSTED      -> SKIP (no model available)
  PARSE_FAIL / STAGING_FAIL / ERROR / WARN   -> WARN (ran, but something off)
  PRODUCTION-BLOCKED                         -> SKIP unless JSC_VISION_PROD_OK=1
  OK                                         -> PASS
  (anything unrecognized)                    -> WARN (flag, never silently pass)
"""
from __future__ import annotations

import os

from ..runner import StepResult

_SKIP = ("GEMINI_UNAVAILABLE", "MODELS_EXHAUSTED")
_WARN = ("WARN", "PARSE_FAIL", "STAGING_FAIL", "ERROR")


def disposition_for(status: str) -> str:
    if status in _SKIP:
        return "SKIP"
    if status == "PRODUCTION-BLOCKED":
        return "PASS" if os.environ.get("JSC_VISION_PROD_OK") == "1" else "SKIP"
    if status in _WARN:
        return "WARN"
    if status == "OK":
        return "PASS"
    return "WARN"  # unknown status — flag, never silently pass


def vision_step(name: str, status: str, detail: str = "") -> StepResult:
    """Build a StepResult from a vision status using disposition_for."""
    return StepResult(step_name=name, status=disposition_for(status),
                      fidelity="BACKEND_DIAGNOSTIC",
                      detail=f"vision:{status} {detail}".strip(),
                      side_effects={"vision_status": status})
