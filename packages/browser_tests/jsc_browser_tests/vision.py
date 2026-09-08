"""Vision dispatcher — Gemini-3 vision analysis of browser-tester screenshots.

Used by `qa_orchestrator.dispatch_vision` to surface UI-failure findings
the headless Playwright runner can't catch via DOM-only checks: error
toasts, "missing field" rendering, layout differences across profiles,
cascading-picklist child-not-loaded states, etc.

Architectural constraints (discovered the hard way during v7.15.0 design):

1. **gemini CLI workspace sandbox**: `gemini @<path>` rejects paths outside
   `the selected private client workspace` AND paths matching `.gitignore` patterns.
   Browser tester writes screenshots to `<selected-client>/state/qa-tests/`
   (operator-private, outside workspace) — those paths are unreachable to
   `gemini @file`. Vision staging copies the screenshot into
   `packages/browser_tests/jsc_browser_tests/_vision_staging/` (workspace-
   tracked, NOT gitignored), invokes gemini, then deletes the staged file.

2. **Gemini optional**: per model-orchestration.md `claude_only` env, the
   operator may not have `gemini` installed. dispatcher emits
   GEMINI_UNAVAILABLE with screenshot paths so operator can review manually.

3. **Server-side capacity exhaustion**: `gemini-3-pro-preview` and
   `gemini-2.5-pro` periodically return 500/MODEL_CAPACITY_EXHAUSTED from
   Google's CodeAssist endpoint (NOT a per-user quota). Module walks a
   fallback chain and emits MODELS_EXHAUSTED with the staged path retained
   for retry if every model 500s.

4. **Anti-hallucination**: prompt forbids speculation. Each finding MUST
   cite a coordinate region or visible text. Findings without evidence are
   dropped at parse time.
"""

from __future__ import annotations
from jsc_common.workspace import workspace_root, state_dir

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_MODEL_CHAIN = (
    "gemini-3-pro-preview",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
)
DEFAULT_TIMEOUT_S = 90
STAGING_TTL_S = 3600  # 1h — staging_gc() removes anything older
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"  # codex-R1-P2-04: magic-bytes check before staging
PROMPT_INJECTION_SAFE_RE = re.compile(r"[^A-Za-z0-9._\-]")


@dataclass
class VisionFinding:
    severity: str  # 'P0' | 'P1' | 'P2' | 'INFO'
    category: str  # 'error_toast' | 'missing_field' | 'layout_anomaly' | 'profile_mismatch' | 'other'
    description: str
    evidence: str  # quoted visible text OR coordinate region


@dataclass
class VisionAnalysisResult:
    screenshot_path: str
    status: str  # 'OK' | 'WARN' | 'ERROR' | 'GEMINI_UNAVAILABLE' | 'MODELS_EXHAUSTED' | 'PARSE_FAIL' | 'STAGING_FAIL'
    findings: list[VisionFinding] = field(default_factory=list)
    model: str = ""
    duration_seconds: float = 0.0
    raw_response: str = ""
    detail: str = ""  # operator-facing summary

    def to_dict(self) -> dict:
        return {
            "screenshot_path": self.screenshot_path,
            "status": self.status,
            "model": self.model,
            "duration_seconds": round(self.duration_seconds, 2),
            "findings": [
                {
                    "severity": f.severity,
                    "category": f.category,
                    "description": f.description,
                    "evidence": f.evidence,
                }
                for f in self.findings
            ],
            "detail": self.detail,
        }


def gemini_available() -> bool:
    return shutil.which("gemini") is not None


def staging_gc() -> int:
    """Remove staged .png files older than STAGING_TTL_S. Returns count."""
    if not state_dir("vision-staging").exists():
        return 0
    now = time.time()
    removed = 0
    for png in state_dir("vision-staging").glob("*.png"):
        try:
            if now - png.stat().st_mtime > STAGING_TTL_S:
                png.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def _safe_stem(*parts: str) -> str:
    """Build a safe filename stem from arbitrary input.

    Collapses unsafe characters AND any consecutive dots (`..`) to `_` —
    not because `..` would actually traverse (the staged path is built via
    `Path` concat, not string-join), but to keep audit logs tidy.
    """
    raw = "-".join(p for p in parts if p)
    cleaned = SAFE_NAME_RE.sub("_", raw)
    cleaned = re.sub(r"\.{2,}", "_", cleaned)
    return cleaned[:80] or "screenshot"


def _sanitize_for_prompt(value: str, *, max_len: int = 80) -> str:
    """Sanitize a string before interpolating it into the gemini prompt.

    Closes R1 gemini-R1-P1-08: flow_name / profile / step_name come from
    operator slash-command args (trustworthy) AND screenshot filenames
    (indirect operator control). Reject any character not in the safe set
    so a malicious filename can't inject "ignore previous instructions"
    into the audit prompt body.
    """
    if not value:
        return "unknown"
    return PROMPT_INJECTION_SAFE_RE.sub("_", value)[:max_len] or "unknown"


def _is_real_png(path: Path) -> bool:
    """Check the first 8 bytes match the PNG magic header.

    Closes R1 codex-R1-P2-04: staging accepts ANY readable file with .png
    extension — confirmed in audit by staging plain-text content as fake.png.
    Magic-bytes check rejects mis-typed inputs (and symlinks resolving to
    non-PNG content) before they're sent to Gemini.
    """
    try:
        with open(path, "rb") as f:
            return f.read(len(PNG_MAGIC)) == PNG_MAGIC
    except OSError:
        return False


def _stage_screenshot(src_abs: Path, *, flow_name: str, profile: str, step_name: str) -> Path | None:
    """Copy screenshot into workspace-local staging dir. Returns staged path or None.

    Validates that src is a real PNG (not a symlinked secret-file with .png
    suffix) and uses a collision-resistant filename (uuid suffix instead of
    1-second timestamp).
    """
    try:
        resolved = src_abs.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not resolved.is_file():
        return None
    # codex-R1-P2-04: magic-bytes check
    if not _is_real_png(resolved):
        return None
    state_dir("vision-staging").mkdir(parents=True, exist_ok=True)
    # codex-R1-P2-03: uuid suffix prevents same-second collisions
    stem = _safe_stem(flow_name, profile, step_name, uuid.uuid4().hex[:12])
    dst = state_dir("vision-staging") / f"{stem}.png"
    try:
        shutil.copy2(resolved, dst)
    except OSError:
        return None
    return dst


def _build_prompt(*, flow_name: str, profile: str, step_name: str, staged_rel: str) -> str:
    """Construct the vision-analysis prompt with anti-hallucination guards.

    All caller-supplied strings (flow_name, profile, step_name) are sanitized
    via _sanitize_for_prompt to prevent prompt-injection from malicious
    screenshot filenames (R1 gemini-R1-P1-08).
    """
    safe_flow = _sanitize_for_prompt(flow_name)
    safe_profile = _sanitize_for_prompt(profile)
    safe_step = _sanitize_for_prompt(step_name)
    return f"""You are a Salesforce Lightning UI auditor. Analyze the screenshot at @{staged_rel}.

CONTEXT:
- Flow: {safe_flow}
- Profile: {safe_profile}
- Step: {safe_step}

OUTPUT RULES (STRICT):
- Output ONLY valid JSON. No markdown, no code fences, no commentary.
- Output MUST start with {{ and end with }}.
- Use ONLY these exact keys: status, findings.
- Each finding MUST include severity (P0|P1|P2|INFO), category, description, evidence.
- evidence MUST quote VISIBLE TEXT from the screenshot OR cite a coordinate region.
- DO NOT speculate. If you cannot identify a specific issue with quoted evidence, omit the finding.
- Empty findings array is correct when the page renders cleanly.

WHAT TO FLAG:
- P0: error toasts/banners (red), 500 pages, "Insufficient Privileges", "Page Not Found"
- P1: empty required fields with red asterisk + no value, broken layout (overlapping elements), missing buttons that should be present per Salesforce Lightning conventions
- P2: cosmetic issues (truncated labels, overflow), minor alignment
- INFO: informational notes worth recording

WHAT TO IGNORE:
- Normal Salesforce chrome (App Launcher, global search, profile menu)
- Empty optional fields
- Default Lightning theme colors

REQUIRED JSON SCHEMA:
{{
  "status": "OK" | "WARN" | "ERROR",
  "findings": [
    {{"severity": "P0", "category": "error_toast", "description": "...", "evidence": "..."}}
  ]
}}
"""


def _invoke_gemini(
    prompt: str, *, model: str, timeout_s: int,
) -> tuple[int, str, str, str]:
    """Invoke gemini CLI. Returns (exit_code, stdout, stderr, error_class).

    error_class is one of:
        '' (success or generic non-zero)
        'TIMEOUT', 'NOT_FOUND', 'AUTH_FAILED', 'CAPACITY'

    Closes R1 codex-R1-P2-02: distinguish auth/capacity/timeout from each
    other instead of conflating all non-zero exits as MODELS_EXHAUSTED.
    """
    cmd = ["gemini", "-m", model, "-p", prompt]
    try:
        p = subprocess.run(
            cmd,
            cwd=str(workspace_root()),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            input="",
        )
        err_class = ""
        stderr_low = (p.stderr or "").lower()
        if p.returncode != 0:
            if "auth" in stderr_low or "unauthorized" in stderr_low or "401" in stderr_low or "403" in stderr_low:
                err_class = "AUTH_FAILED"
            elif "model_capacity_exhausted" in stderr_low or "capacity" in stderr_low or "rate limit" in stderr_low or "quota" in stderr_low or "500" in stderr_low or "503" in stderr_low:
                err_class = "CAPACITY"
        return p.returncode, p.stdout, p.stderr, err_class
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout_s}s", "TIMEOUT"
    except FileNotFoundError:
        return 127, "", "gemini binary not found", "NOT_FOUND"


def _parse_response(raw: str) -> tuple[str, list[VisionFinding], str]:
    """Parse gemini response. Returns (status, findings, parse_detail)."""
    if not raw or not raw.strip():
        return "ERROR", [], "empty response"

    # Find first { ... } JSON block (gemini sometimes prepends a banner line)
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return "PARSE_FAIL", [], f"no JSON object in response (got {len(raw)} chars)"

    candidate = raw[start : end + 1]
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as e:
        return "PARSE_FAIL", [], f"json decode error: {e}"

    status = str(payload.get("status", "")).upper()
    if status not in ("OK", "WARN", "ERROR"):
        status = "WARN"

    raw_findings = payload.get("findings") or []
    if not isinstance(raw_findings, list):
        return "PARSE_FAIL", [], "findings is not a list"

    parsed: list[VisionFinding] = []
    for f in raw_findings:
        if not isinstance(f, dict):
            continue
        sev = str(f.get("severity", "")).upper()
        if sev not in ("P0", "P1", "P2", "INFO"):
            continue
        cat = str(f.get("category", "other")).strip() or "other"
        desc = str(f.get("description", "")).strip()
        evid = str(f.get("evidence", "")).strip()
        if not desc or not evid:
            # Anti-hallucination: drop findings without description AND evidence
            continue
        parsed.append(
            VisionFinding(severity=sev, category=cat, description=desc, evidence=evid)
        )

    return status, parsed, "ok"


def analyze_screenshot(
    screenshot_path: str | Path,
    *,
    flow_name: str = "unknown",
    profile: str = "admin",
    step_name: str = "unknown",
    models: tuple[str, ...] = DEFAULT_MODEL_CHAIN,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> VisionAnalysisResult:
    """Stage a screenshot + invoke gemini vision + parse findings.

    On success returns status OK/WARN/ERROR with parsed findings. On
    gemini absence returns GEMINI_UNAVAILABLE. On all-models-500 returns
    MODELS_EXHAUSTED. On JSON parse failure returns PARSE_FAIL with raw
    response retained in result.raw_response for operator inspection.
    """
    src = Path(screenshot_path).resolve()
    started = time.monotonic()

    # Best-effort GC of stale staged files
    staging_gc()

    if not gemini_available():
        return VisionAnalysisResult(
            screenshot_path=str(src),
            status="GEMINI_UNAVAILABLE",
            detail=(
                "Gemini CLI not installed. Vision analysis is OPTIONAL per "
                "model-orchestration.md. Operator may review screenshot manually "
                f"at {src}."
            ),
        )

    staged = _stage_screenshot(src, flow_name=flow_name, profile=profile, step_name=step_name)
    if staged is None:
        return VisionAnalysisResult(
            screenshot_path=str(src),
            status="STAGING_FAIL",
            detail=f"could not stage screenshot from {src} (file missing or not readable)",
        )

    # Path passed to gemini @file syntax must be relative to workspace cwd
    staged_rel = staged.relative_to(workspace_root()).as_posix()
    prompt = _build_prompt(
        flow_name=flow_name, profile=profile, step_name=step_name, staged_rel=staged_rel
    )

    last_stderr = ""
    used_model = ""
    raw_stdout = ""
    last_err_class = ""
    for model in models:
        rc, stdout, stderr, err_class = _invoke_gemini(prompt, model=model, timeout_s=timeout_s)
        used_model = model
        raw_stdout = stdout
        last_stderr = stderr
        last_err_class = err_class
        if rc == 0 and stdout.strip():
            status, findings, parse_detail = _parse_response(stdout)
            duration = time.monotonic() - started
            # Cleanup staged file on success
            try:
                staged.unlink()
            except OSError:
                pass
            return VisionAnalysisResult(
                screenshot_path=str(src),
                status=status,
                findings=findings,
                model=model,
                duration_seconds=duration,
                raw_response=stdout[:8192],
                detail=parse_detail,
            )
        if err_class == "NOT_FOUND":
            # gemini disappeared between availability check and invocation
            try:
                staged.unlink()
            except OSError:
                pass
            return VisionAnalysisResult(
                screenshot_path=str(src),
                status="GEMINI_UNAVAILABLE",
                detail="gemini binary missing at invocation",
            )
        # R1 codex-R1-P2-02: deterministic non-capacity failures (e.g. auth)
        # should NOT be treated as exhaustion — fail fast on first model
        if err_class == "AUTH_FAILED":
            try:
                staged.unlink()
            except OSError:
                pass
            return VisionAnalysisResult(
                screenshot_path=str(src),
                status="AUTH_FAILED",
                model=model,
                duration_seconds=time.monotonic() - started,
                raw_response=raw_stdout[:2048],
                detail=f"AUTH_FAILED: {stderr[:512]}",
            )
        # CAPACITY / TIMEOUT / generic non-zero → walk fallback chain

    # All models exhausted — preserve staged file for operator retry
    duration = time.monotonic() - started
    return VisionAnalysisResult(
        screenshot_path=str(src),
        status="MODELS_EXHAUSTED",
        model=used_model,
        duration_seconds=duration,
        raw_response=raw_stdout[:2048],
        detail=(
            f"All gemini models exhausted ({', '.join(models)}). Last err_class="
            f"{last_err_class or 'unknown'}; stderr: {last_stderr[:512]}. "
            f"Staged file retained at {staged} for retry."
        ),
    )


_FILENAME_PROFILE_RE = re.compile(r"^[a-z0-9]+_(?P<profile>[a-z0-9_]+?)_[a-z0-9_]+$", re.IGNORECASE)


def _profile_from_filename(stem: str, default: str) -> str:
    """Extract profile name from filename per smoke_<profile>_home.png convention.

    Closes R1 codex-R1-P2-08 (escalated to P1): dispatch_vision was hardcoding
    profile='admin' even for multiprofile runs. Browser tester writes
    `smoke_<profile>_home.png` — parse the profile slot when present.
    """
    m = _FILENAME_PROFILE_RE.match(stem)
    if m:
        return m.group("profile")
    return default


def analyze_run_screenshots(
    run_dir: str | Path,
    *,
    flow_name: str,
    profile: str = "admin",
    models: tuple[str, ...] = DEFAULT_MODEL_CHAIN,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> list[VisionAnalysisResult]:
    """Walk a runner output dir; analyze every .png screenshot.

    `profile` is the DEFAULT used when a screenshot's filename doesn't match
    the smoke_<profile>_home.png convention. Per-screenshot profile is read
    from the filename when available (closes R1 codex-R1-P2-08).
    """
    rd = Path(run_dir)
    if not rd.exists() or not rd.is_dir():
        return []
    results: list[VisionAnalysisResult] = []
    for png in sorted(rd.glob("*.png")):
        step = png.stem
        per_shot_profile = _profile_from_filename(step, default=profile)
        results.append(
            analyze_screenshot(
                png,
                flow_name=flow_name,
                profile=per_shot_profile,
                step_name=step,
                models=models,
                timeout_s=timeout_s,
            )
        )
    return results
