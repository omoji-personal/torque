"""harness.py — replay a prompt fixture against an LLM transport + validate JSON output.

Fixture layout (one per regression target):

  fixtures/<name>/
    spec.json         — TargetSpec(s) declaring expected output shape
    prompt.txt        — the prompt template body (with {{INPUT}} placeholder)
    input.txt         — the frozen $Flow.Prompt content to substitute
    [expected.json]   — optional reference output for diff (informational)

Replay flow:
  1. Read spec.json + prompt.txt + input.txt
  2. Substitute {{INPUT}} into the prompt
  3. Dispatch via gemini CLI (Pro → Flash fallback) with workspace cwd
  4. safe_parse_json the response
  5. validate_against_spec — emit ValidationResult + ReplayResult

Per .claude/rules/ai-offering-patterns.md, this exercises the "one
Prompt Builder call → strict JSON → local parse" recipe end-to-end
WITHOUT requiring a live Salesforce org (the parse-side validation
catches schema drift offline).
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

from .contracts import (
    TargetSpec,
    ValidationFinding,
    ValidationResult,
    parse_target_spec_yaml,
    safe_parse_json,
    validate_against_spec,
)


DEFAULT_MODEL_CHAIN = (
    "gemini-3-pro-preview",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
)
DEFAULT_TIMEOUT_S = 90
INPUT_PLACEHOLDER = "{{INPUT}}"

# R1 codex-P1-03: Salesforce Trust Layer ceiling — 65K tokens × ~4 chars/token
# minus output reservation + scaffolding overhead = ~200K char planning ceiling
# per ai-offering-patterns.md. Operator may override via JSC_AI_PROMPT_MAX_CHARS.
DEFAULT_MAX_PROMPT_CHARS = 200_000

# R1 CONSENSUS-2: macOS execve ARG_MAX is ~256KB. Pass prompt via @file rather
# than argv when over this safety threshold. Operator may override via env var.
ARGV_SAFE_THRESHOLD_CHARS = 100_000  # well under ARG_MAX (~256KB on macOS, ~128KB on some BSDs)


def _max_prompt_chars() -> int:
    raw = os.environ.get("JSC_AI_PROMPT_MAX_CHARS")
    if raw and raw.isdigit():
        return int(raw)
    return DEFAULT_MAX_PROMPT_CHARS


def _argv_safe_threshold() -> int:
    raw = os.environ.get("JSC_AI_PROMPT_ARGV_THRESHOLD_CHARS")
    if raw and raw.isdigit():
        return int(raw)
    return ARGV_SAFE_THRESHOLD_CHARS


@dataclass
class Fixture:
    name: str
    fixture_dir: Path
    specs: list[TargetSpec]
    prompt_template: str
    input_text: str


@dataclass
class ReplayResult:
    fixture_name: str
    status: str  # 'PASS' | 'FAIL' | 'PARSE_FAIL' | 'GEMINI_UNAVAILABLE' | 'MODELS_EXHAUSTED' | 'FIXTURE_INVALID'
    model: str = ""
    duration_seconds: float = 0.0
    raw_response: str = ""
    validation: ValidationResult | None = None
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "fixture_name": self.fixture_name,
            "status": self.status,
            "model": self.model,
            "duration_seconds": round(self.duration_seconds, 2),
            "validation": self.validation.to_dict() if self.validation else None,
            "detail": self.detail,
        }


def gemini_available() -> bool:
    return shutil.which("gemini") is not None


def load_fixture(fixture_dir: str | Path) -> Fixture:
    """Load a fixture from disk. Raises ValueError on schema problems."""
    fd = Path(fixture_dir).resolve()
    if not fd.exists() or not fd.is_dir():
        raise ValueError(f"fixture dir not found: {fd}")

    spec_path = fd / "spec.json"
    prompt_path = fd / "prompt.txt"
    input_path = fd / "input.txt"
    for p in (spec_path, prompt_path, input_path):
        if not p.exists():
            raise ValueError(f"fixture missing required file: {p}")

    spec_raw = json.loads(spec_path.read_text(encoding="utf-8"))
    if isinstance(spec_raw, dict) and "specs" in spec_raw:
        spec_rows = spec_raw["specs"]
    elif isinstance(spec_raw, list):
        spec_rows = spec_raw
    else:
        spec_rows = [spec_raw]
    specs = [parse_target_spec_yaml(row) for row in spec_rows]

    prompt_template = prompt_path.read_text(encoding="utf-8")
    input_text = input_path.read_text(encoding="utf-8")

    return Fixture(
        name=fd.name,
        fixture_dir=fd,
        specs=specs,
        prompt_template=prompt_template,
        input_text=input_text,
    )


def _build_prompt(template: str, input_text: str) -> str:
    """Substitute {{INPUT}} into the prompt template."""
    if INPUT_PLACEHOLDER in template:
        return template.replace(INPUT_PLACEHOLDER, input_text)
    # Tolerate templates that don't have a placeholder; append input
    return template.rstrip() + "\n\n" + input_text


def _stage_prompt_to_workspace(prompt: str) -> Path:
    """Write the rendered prompt to a workspace-tracked tempfile so gemini's
    @file syntax can read it. Caller is responsible for cleanup.

    Closes R1 CONSENSUS-2 / codex-R1-P1-04: argv length limited; large prompts
    via @file avoid OSError [Errno 7] Argument list too long.
    """
    state_dir("prompt-staging").mkdir(parents=True, exist_ok=True)
    name = f"prompt-{uuid.uuid4().hex[:16]}.txt"
    path = state_dir("prompt-staging") / name
    path.write_text(prompt, encoding="utf-8")
    return path


def _invoke_gemini(
    prompt: str, *, model: str, timeout_s: int,
) -> tuple[int, str, str, str]:
    """Invoke gemini CLI. Returns (exit_code, stdout, stderr, error_class).

    error_class is one of:
        '' (no specific class, success or generic non-zero)
        'TIMEOUT' (subprocess.TimeoutExpired)
        'NOT_FOUND' (gemini binary missing)
        'ARGV_TOO_LONG' (OSError E2BIG before exec — caught structurally)
        'PROMPT_TOO_LARGE' (gemini stderr matches "Prompt size exceeds")
        'AUTH_FAILED' (gemini stderr matches auth-failure signatures)
        'CAPACITY' (gemini stderr matches capacity-exhausted signatures)

    Closes R1 codex-R1-P2-01/02: distinct error classes instead of conflating
    everything as MODELS_EXHAUSTED.
    """
    threshold = _argv_safe_threshold()
    staged_path: Path | None = None
    if len(prompt) > threshold:
        # Stage to workspace tempfile + invoke via @file
        staged_path = _stage_prompt_to_workspace(prompt)
        rel = staged_path.relative_to(workspace_root()).as_posix()
        cmd = ["gemini", "-m", model, "-p", f"@{rel}"]
    else:
        cmd = ["gemini", "-m", model, "-p", prompt]

    try:
        try:
            p = subprocess.run(
                cmd,
                cwd=str(workspace_root()),
                capture_output=True,
                text=True,
                timeout=timeout_s,
                input="",
            )
        except OSError as e:
            if getattr(e, "errno", None) == 7:  # E2BIG
                return 1, "", str(e)[:200], "ARGV_TOO_LONG"
            return 1, "", str(e)[:200], "OS_ERROR"

        err_class = ""
        stderr_low = (p.stderr or "").lower()
        if p.returncode != 0:
            if "prompt size exceeds" in stderr_low or "context length" in stderr_low:
                err_class = "PROMPT_TOO_LARGE"
            elif "auth" in stderr_low or "unauthorized" in stderr_low or "401" in stderr_low or "403" in stderr_low:
                err_class = "AUTH_FAILED"
            elif "model_capacity_exhausted" in stderr_low or "capacity" in stderr_low or "rate limit" in stderr_low or "quota" in stderr_low or "500" in stderr_low or "503" in stderr_low:
                err_class = "CAPACITY"
        return p.returncode, p.stdout, p.stderr, err_class
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout_s}s", "TIMEOUT"
    except FileNotFoundError:
        return 127, "", "gemini binary not found", "NOT_FOUND"
    finally:
        if staged_path and staged_path.exists():
            try:
                staged_path.unlink()
            except OSError:
                pass


def replay_fixture(
    fixture: Fixture,
    *,
    models: tuple[str, ...] = DEFAULT_MODEL_CHAIN,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> ReplayResult:
    """Dispatch the fixture against gemini, parse output, validate shape."""
    started = time.monotonic()
    if not gemini_available():
        return ReplayResult(
            fixture_name=fixture.name,
            status="GEMINI_UNAVAILABLE",
            duration_seconds=time.monotonic() - started,
            detail=(
                "gemini CLI not installed. AI prompt regression is OPTIONAL per "
                "model-orchestration.md. Operator may invoke claude_only fallback "
                "manually by pasting the prompt into Claude + running validation."
            ),
        )

    prompt = _build_prompt(fixture.prompt_template, fixture.input_text)

    # R1 codex-P1-03: pre-flight Trust Layer ceiling guard
    max_chars = _max_prompt_chars()
    if len(prompt) > max_chars:
        return ReplayResult(
            fixture_name=fixture.name,
            status="PROMPT_TOO_LARGE",
            duration_seconds=time.monotonic() - started,
            detail=(
                f"Rendered prompt is {len(prompt):,} chars; exceeds "
                f"JSC_AI_PROMPT_MAX_CHARS={max_chars:,} (Salesforce Trust Layer "
                f"~200K char ceiling per ai-offering-patterns.md). Refusing to "
                f"invoke; tighten the data provider OR raise the env var."
            ),
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
            payload, parse_err = safe_parse_json(stdout)
            if payload is None:
                return ReplayResult(
                    fixture_name=fixture.name,
                    status="PARSE_FAIL",
                    model=model,
                    duration_seconds=time.monotonic() - started,
                    raw_response=stdout[:8192],
                    detail=parse_err,
                )
            validation = validate_against_spec(payload, fixture.specs)
            return ReplayResult(
                fixture_name=fixture.name,
                status=validation.status,
                model=model,
                duration_seconds=time.monotonic() - started,
                raw_response=stdout[:8192],
                validation=validation,
                detail=(
                    f"validated {len(fixture.specs)} target spec(s); "
                    f"{len(validation.findings)} findings"
                ),
            )
        if err_class == "NOT_FOUND":
            return ReplayResult(
                fixture_name=fixture.name,
                status="GEMINI_UNAVAILABLE",
                duration_seconds=time.monotonic() - started,
                detail="gemini binary missing at invocation",
            )
        # R1 codex-R1-P2-01: deterministic non-capacity failures should NOT
        # be treated as exhaustion — fail fast on the first model
        if err_class in ("PROMPT_TOO_LARGE", "AUTH_FAILED", "ARGV_TOO_LONG"):
            return ReplayResult(
                fixture_name=fixture.name,
                status=err_class,
                model=model,
                duration_seconds=time.monotonic() - started,
                raw_response=raw_stdout[:2048],
                detail=f"{err_class}: {stderr[:512]}",
            )
        # CAPACITY / TIMEOUT / generic non-zero → walk fallback chain

    # Reaching here = all models exhausted with capacity/timeout/generic errors
    return ReplayResult(
        fixture_name=fixture.name,
        status="MODELS_EXHAUSTED",
        model=used_model,
        duration_seconds=time.monotonic() - started,
        raw_response=raw_stdout[:2048],
        detail=(
            f"All gemini models exhausted ({', '.join(models)}). "
            f"Last err_class={last_err_class or 'unknown'}; "
            f"stderr: {last_stderr[:512]}"
        ),
    )


def replay_directory(
    fixtures_root: str | Path,
    *,
    models: tuple[str, ...] = DEFAULT_MODEL_CHAIN,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> list[ReplayResult]:
    """Walk a directory of fixtures (one per subdir) + replay each."""
    root = Path(fixtures_root)
    if not root.exists() or not root.is_dir():
        return []
    out: list[ReplayResult] = []
    for fd in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        try:
            fixture = load_fixture(fd)
        except (ValueError, json.JSONDecodeError) as e:
            out.append(ReplayResult(
                fixture_name=fd.name,
                status="FIXTURE_INVALID",
                detail=str(e),
            ))
            continue
        out.append(replay_fixture(fixture, models=models, timeout_s=timeout_s))
    return out
