"""intent_marker.py — TTL token mint/lock/consume for deploy_gate bypass.

Closes plan-v5 Closure 3 (mint side; deploy_gate.py is the validate side).
Codex-R5-P1-2 fixes applied:
  - Manual-bypass reason regex uses STRIPPED non-whitespace fullmatch
    (won't accept 'JIRA-1234:      ' whitespace-only padding)
  - Owner field uses pwd.getpwuid(os.getuid()).pw_name (not USER env var)
  - File mode 0o600 enforced at mint
"""

from __future__ import annotations
from jsc_common.workspace import state_dir

import hashlib
import json
import os
import re
import time
from pathlib import Path

if os.name != "nt":
    import pwd

from . import bundle


DEFAULT_TOKEN_PATH = None  # legacy helper, never consulted by normal operations

def _token_path():
    return state_dir("legacy-tokens") / ".deploy_intent_token.json"
TOKEN_SCHEMA_VERSION = 1

MAX_TTL_BY_OP_TYPE = {
    "revert": 900,         # 15 min
    "recovery": 1800,      # 30 min
    "manual-bypass": 900,  # 15 min
}

# Codex-R5-P1-2 fix: regex matches stripped non-whitespace text only.
# 'JIRA-1234:      ' has 5+ chars total but stripped body is empty → reject.
_JIRA_INC_PATTERN = re.compile(r"^(?:JIRA|INC)-\d+:\s*(\S.*)$")
_AD_HOC_PATTERN = re.compile(r"^ad-hoc:\s*(\S.*)$")


def _current_user_name() -> str:
    """Kernel-derived username; NOT spoofable via USER env var.

    Windows has neither getuid() nor pwd; USERNAME is the best available
    signal there (Windows' security model does not offer a POSIX-equivalent
    tamper-resistant lookup without pywin32).
    """
    if os.name == "nt":
        return os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"
    return pwd.getpwuid(os.getuid()).pw_name


def is_structured_reason(reason: str) -> bool:
    """True if `reason` matches one of the accepted structured forms.

    Forms:
      JIRA-NNNN: <body>   (body must have ≥5 non-whitespace chars after stripping)
      INC-NNNN: <body>    (same)
      ad-hoc: <body>      (body must have ≥20 non-whitespace chars after stripping)

    Codex-R5-P1-2: regex uses fullmatch on stripped non-whitespace body, so
    'JIRA-1234:      ' (whitespace-only body) is rejected.
    """
    if not isinstance(reason, str):
        return False
    stripped = reason.strip()

    m = _JIRA_INC_PATTERN.match(stripped)
    if m:
        body = m.group(1).strip()
        return len(body) >= 5

    m = _AD_HOC_PATTERN.match(stripped)
    if m:
        body = m.group(1).strip()
        return len(body) >= 20

    return False


def fingerprint_command(command: str) -> str:
    """sha256 hex digest of command, prefixed with sha256: per token format."""
    return "sha256:" + hashlib.sha256(command.encode("utf-8")).hexdigest()


def mint(
    operation_type: str,
    org_id_18: str,
    command_fingerprint: str | None = None,
    command: str | None = None,
    reason: str = "",
    expiry_seconds: int | None = None,
    operator: str | None = None,
    session_id: str | None = None,
    org_alias: str | None = None,
    target_path: Path | None = None,
) -> Path:
    """Mint a TTL bypass token.

    Args:
        operation_type: 'revert' | 'recovery' | 'manual-bypass'
        org_id_18: 18-char Salesforce org id the token is bound to
        command_fingerprint: pre-computed fingerprint, OR
        command: raw command string to fingerprint here
        reason: human-readable reason; for manual-bypass MUST match
                is_structured_reason
        expiry_seconds: TTL; capped at MAX_TTL_BY_OP_TYPE[operation_type]
        operator: defaults to kernel uid name (NOT spoofable USER env)
        session_id: optional session correlation id
        org_alias: the --org alias the token is bound to; revert_warning_pretool
                   compares this (cheap, in-budget) since it receives the same
                   alias from the command (deploy_gate uses org_id_18 + resolve)
        target_path: optional override for token path (testing)

    Returns:
        Path to the minted token file.

    Raises:
        ValueError: invalid operation_type, expiry exceeds cap, or
                    manual-bypass reason fails is_structured_reason.
    """
    if operation_type not in MAX_TTL_BY_OP_TYPE:
        raise ValueError(
            f"unsupported operation_type: {operation_type!r} "
            f"(valid: {list(MAX_TTL_BY_OP_TYPE)})"
        )
    max_ttl = MAX_TTL_BY_OP_TYPE[operation_type]
    if expiry_seconds is None:
        expiry_seconds = max_ttl
    if expiry_seconds > max_ttl:
        raise ValueError(
            f"expiry_seconds {expiry_seconds} exceeds max {max_ttl} "
            f"for operation_type {operation_type!r}"
        )
    if expiry_seconds <= 0:
        raise ValueError(f"expiry_seconds must be positive (got {expiry_seconds})")

    if operation_type == "manual-bypass" and not is_structured_reason(reason):
        raise ValueError(
            "manual-bypass reason must be structured: "
            "'JIRA-NNNN: <text>' (≥5 non-ws chars) OR "
            "'INC-NNNN: <text>' (≥5 non-ws chars) OR "
            "'ad-hoc: <text>' (≥20 non-ws chars)"
        )

    if command_fingerprint is None:
        if command is None:
            command_fingerprint = "sha256:" + ("0" * 64)  # placeholder; must match fingerprint at validate
        else:
            command_fingerprint = fingerprint_command(command)

    operator = operator if operator is not None else _current_user_name()

    now = time.time()
    issued_iso = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now))
    expiry_iso = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now + expiry_seconds))

    token = {
        "schema_version": TOKEN_SCHEMA_VERSION,
        "operation_type": operation_type,
        "org_id_18": org_id_18,
        "command_fingerprint": command_fingerprint,
        "reason": reason,
        "expiry_iso": expiry_iso,
        "issued_at_iso": issued_iso,
        "operator": operator,
        "session_id": session_id,
        "org_alias": org_alias,
    }

    if target_path is None:
        target_path = _token_path()

    bundle.atomic_write_json(target_path, token, mode=0o600)
    return target_path


def revoke(target_path: Path | None = None) -> bool:
    """Delete the token file. Returns True if a file was deleted."""
    if target_path is None:
        target_path = _token_path()
    try:
        target_path.unlink()
        return True
    except FileNotFoundError:
        return False


def show(target_path: Path | None = None) -> dict | None:
    """Read the token (without consuming). Returns None if missing/invalid."""
    if target_path is None:
        target_path = _token_path()
    try:
        return json.loads(target_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
