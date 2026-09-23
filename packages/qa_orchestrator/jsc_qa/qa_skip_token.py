"""qa_skip_token.py — JSC_QA_SKIP_TOKEN_PATH validation.

Mirrors revert TTL token pattern (per design-v4 Closure 6 + Codex-R1-P2-1).
SEPARATE namespace from JSC_REVERT_TOKEN_PATH — different authorities
(skipping verification ≠ bypassing snapshot).

Operator mints via `/qa-token grant`. Single-use atomic consume per validation.
"""

from __future__ import annotations
from jsc_common.workspace import state_dir

import json
import os
import time
from datetime import datetime
from pathlib import Path

if os.name != "nt":
    import pwd


DEFAULT_TOKEN_PATH = None  # legacy helper, never consulted by normal operations

def _token_path():
    return state_dir("legacy-tokens") / ".qa_skip_token.json"
TOKEN_SCHEMA_VERSION = 1

# TTL caps per operation_type (per design-v4)
MAX_TTL_BY_OP_TYPE = {
    "skip_one_off": 900,        # 15 min
    "skip_change_type": 3600,   # 60 min
    "skip_surface": 86400,      # 24 hours
}


def _current_user_name() -> str:
    """Windows has neither getuid() nor pwd; USERNAME is the best available
    signal there (see jsc_revert.intent_marker._current_user_name)."""
    if os.name == "nt":
        return os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"
    return pwd.getpwuid(os.getuid()).pw_name


def _iso_to_epoch(iso: str) -> float | None:
    try:
        s = iso.replace("Z", "+00:00") if iso.endswith("Z") else iso
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError, AttributeError):
        return None


def mint(
    operation_type: str,
    org_id_18: str,
    skip_target: str,
    reason: str,
    expiry_seconds: int | None = None,
    operator: str | None = None,
    target_path: Path | None = None,
) -> Path:
    """Mint a QA skip token. Mirrors intent_marker.mint() pattern.

    Args:
        operation_type: 'skip_one_off' | 'skip_change_type' | 'skip_surface'
        org_id_18: 18-char Salesforce org id
        skip_target: change_type id (e.g., 'A3') OR surface name (e.g., 'Vision')
                     OR command_fingerprint for skip_one_off
        reason: structured reason (per qa-orchestration.md discipline)
        expiry_seconds: TTL; capped at MAX_TTL_BY_OP_TYPE[operation_type]
        operator: defaults to kernel uid name
        target_path: optional override (testing)
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
    if not skip_target or not isinstance(skip_target, str):
        raise ValueError(f"skip_target required (non-empty string)")
    if not reason or not isinstance(reason, str) or len(reason.strip()) < 10:
        raise ValueError(f"reason required (≥10 chars after strip)")

    operator = operator if operator is not None else _current_user_name()
    now = time.time()
    issued_iso = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now))
    expiry_iso = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now + expiry_seconds))

    token = {
        "schema_version": TOKEN_SCHEMA_VERSION,
        "operation_type": operation_type,
        "org_id_18": org_id_18,
        "skip_target": skip_target,
        "reason": reason,
        "expiry_iso": expiry_iso,
        "issued_at_iso": issued_iso,
        "operator": operator,
    }

    if target_path is None:
        # Per audit codex-R5-P1-02: honor JSC_QA_SKIP_TOKEN_PATH so mint
        # writes to the same location validate_for_skip reads.
        env_override = None if os.environ.get("TORQUE_WORKSPACE") else os.environ.get("JSC_QA_SKIP_TOKEN_PATH")
        target_path = Path(env_override) if env_override else _token_path()

    target_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(target_path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps(token).encode())
    finally:
        os.close(fd)
    return target_path


def revoke(target_path: Path | None = None) -> bool:
    if target_path is None:
        env_override = None if os.environ.get("TORQUE_WORKSPACE") else os.environ.get("JSC_QA_SKIP_TOKEN_PATH")
        target_path = Path(env_override) if env_override else _token_path()
    try:
        target_path.unlink()
        return True
    except FileNotFoundError:
        return False


def show(target_path: Path | None = None) -> dict | None:
    target_path = target_path or _token_path()
    try:
        return json.loads(target_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def validate_for_skip(
    org_id_18: str,
    skip_target: str,
    target_path: Path | None = None,
) -> tuple[bool, str]:
    """Validate token for a specific skip request. Returns (is_valid, detail).

    Validates: file mode, ownership, schema, required fields, timing,
    operator match, org match, skip_target match.
    """
    token_path_env = None if os.environ.get("TORQUE_WORKSPACE") else os.environ.get("JSC_QA_SKIP_TOKEN_PATH")
    path = Path(token_path_env) if token_path_env else (target_path or _token_path())

    if not path.exists():
        return False, "no token present"

    try:
        st = path.stat()
    except OSError:
        return False, "cannot stat token file"
    # Windows has no POSIX mode bits or getuid(); this hardening is POSIX-only.
    if os.name != "nt":
        if (st.st_mode & 0o777) != 0o600:
            return False, f"token file mode is {oct(st.st_mode & 0o777)}, required 0o600"
        if st.st_uid != os.getuid():
            return False, f"token file owned by uid {st.st_uid}, current uid is {os.getuid()}"

    try:
        token = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return False, f"unreadable or malformed token: {e}"

    if token.get("schema_version") != TOKEN_SCHEMA_VERSION:
        return False, f"unsupported schema_version: {token.get('schema_version')}"

    required = ("operation_type", "org_id_18", "skip_target", "reason",
                "expiry_iso", "issued_at_iso", "operator")
    for k in required:
        if k not in token:
            return False, f"missing required field: {k}"

    # Operation-type allowlist (QA-SKIP-UNKNOWN-OP, full-repo TAA 2026-05-31).
    # The skip_target branch below is an if/elif chain with no else, so an
    # UNKNOWN operation_type matched none, skipped skip_target validation
    # entirely, and fell through to consume + return True. mint() rejects
    # unknown ops, but a hand-crafted token file must not bypass the validator
    # (the revert-token R6-P1-1 lesson: never trust the file was minted).
    op_type = token["operation_type"]
    if op_type not in MAX_TTL_BY_OP_TYPE:
        return False, f"unsupported operation_type: {op_type!r}"

    # Timing
    now = time.time()
    expiry = _iso_to_epoch(token["expiry_iso"])
    issued = _iso_to_epoch(token["issued_at_iso"])
    if expiry is None or issued is None:
        return False, "unparseable expiry_iso or issued_at_iso"
    if expiry < now:
        return False, f"token expired at {token['expiry_iso']}"
    if issued > now + 60:
        return False, f"issued_at_iso is in the future"
    # TTL cap per operation_type (QA-SKIP-TTL-UNCAPPED, full-repo TAA 2026-05-31).
    # mint() caps TTL at issuance, but a hand-edited token with a 30-day window
    # would otherwise validate as long as it had not yet expired.
    max_ttl = MAX_TTL_BY_OP_TYPE[op_type]
    if (expiry - issued) > max_ttl + 60:  # 60s issuance-jitter slack
        return False, (
            f"TTL {int(expiry - issued)}s exceeds max {max_ttl}s "
            f"for operation_type {op_type!r}"
        )

    # Operator
    current_user = _current_user_name()
    if token["operator"] != current_user:
        return False, f"token operator {token['operator']!r} != current user {current_user!r}"

    # Org
    if token["org_id_18"] != org_id_18:
        return False, f"token bound to org {token['org_id_18']}, request is for {org_id_18}"

    # Skip target. op_type is already allowlist-validated above, so every
    # reachable value has an explicit branch; the trailing else is a
    # belt-and-suspenders guard should the allowlist + branch set ever drift.
    if op_type == "skip_one_off":
        # exact match required (command_fingerprint)
        if token["skip_target"] != skip_target:
            return False, f"token skip_target {token['skip_target']!r} != requested {skip_target!r}"
    elif op_type == "skip_surface":
        # token covers a specific surface; request must specify same surface
        if token["skip_target"] != skip_target:
            return False, f"token covers surface {token['skip_target']!r}, request is for {skip_target!r}"
    elif op_type == "skip_change_type":
        # token covers change_type; request must specify same change_type id
        if token["skip_target"] != skip_target:
            return False, f"token covers change_type {token['skip_target']!r}, request is for {skip_target!r}"
    else:
        return False, f"unsupported operation_type: {op_type!r}"

    # Single-use consume — ATOMIC claim via os.rename (closes the TOCTOU).
    # os.rename is atomic on POSIX local filesystems: of N concurrent callers
    # that all pass the read-only validation above, exactly one rename of `path`
    # succeeds; the rest raise FileNotFoundError, meaning "already consumed by a
    # concurrent validator". The rename winner is therefore the sole consumer,
    # honoring the module's "Single-use atomic consume" contract (mirrors the
    # revert-token precedent). (Audit 2026-05-30 COR-3.)
    # NOTE: JSC_QA_SKIP_TOKEN_PATH should be a local filesystem — os.rename
    # atomicity is not guaranteed on NFS/SMB/some FUSE mounts.
    sentinel = path.with_name(f"{path.name}.consumed.{os.getpid()}.{time.monotonic_ns()}")
    try:
        os.rename(str(path), str(sentinel))
    except FileNotFoundError:
        return False, "token already consumed"
    except OSError as e:
        return False, f"could not consume token: {e}"
    try:
        sentinel.unlink()
    except OSError:
        pass  # sentinel cleanup is best-effort; we are already the sole consumer

    return True, f"valid {op_type} token (consumed)"
