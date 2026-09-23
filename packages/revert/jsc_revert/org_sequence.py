"""org_sequence.py — per-org concurrency lock (real CAS via O_CREAT|O_EXCL).

Closes plan-v5 Closure 2 + Codex-R5-P1-3 (PID validation must NOT fail-open
when cmdline inspection is unavailable).

Lock state stored at: <revert-dir>/<org_id_short>-<alias>/.org_lock.json

Atomic acquisition: O_CREAT|O_EXCL kernel-guaranteed exactly-one winner.
Tagged read result: VALID | EMPTY | MALFORMED | NOT_PRESENT — handles
crash-window where wrapper died between O_EXCL and write (empty lockfile)
+ malformed lockfile (corrupt JSON / missing required fields).

PID validation: tristate WRAPPER | NOT_WRAPPER | UNKNOWN. UNKNOWN means we
couldn't run `ps`/`/proc` to verify the process is actually a JSC wrapper —
in that case treat a same-host owner as NOT-stealable while its identity remains
unknown. Codex empirically reproduced PermissionError on `ps -p` on Darwin
sandbox; v5 stub returned False (treated as NOT_WRAPPER) and stole the
lock. v6 fix returns UNKNOWN explicitly + grace-period blocks the steal.

Empty/malformed lockfile recovery: if the file exists but cannot be parsed
to valid state, check mtime — older than HARD_ABSOLUTE_THRESHOLD → safe to
steal with audit. Younger → wait (caller may be mid-write).

Cross-host locks (different hostname): treated as live within heartbeat
window. Beyond hard-absolute threshold, conservatively steal. Operator
should NOT put JSC_REVERT_DIR on NFS/SMB — local-only is documented.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import NamedTuple

from . import bundle


LOCK_HEARTBEAT_SECONDS = 60
LOCK_STALE_THRESHOLD_SECONDS = 300       # heartbeat freshness gate (5 min)
LOCK_HARD_ABSOLUTE_THRESHOLD_SECONDS = 900   # stale cross-host/unparseable lease threshold
PS_CHECK_TIMEOUT_SECONDS = 2

# Cmdline marker: substring identifying a JSC wrapper process
JSC_WRAPPER_CMDLINE_MARKER = "jsc_revert"
WRAPPER_CMDLINE_MARKERS = (JSC_WRAPPER_CMDLINE_MARKER, "torque.cli", "/torque", "/jsc")


def _win_pid_alive(pid: int) -> bool | None:
    """Windows liveness check via OpenProcess (no subprocess, no dependency).

    Returns True if a process with this pid exists and could be opened, None
    if OpenProcess confirms no such process exists (ERROR_INVALID_PARAMETER),
    or False for anything else (including access denied) - identity can't be
    confirmed either way. False, not a guessed True/None, is deliberate:
    stealing a lock from a still-live operation is the unsafe direction to
    be wrong in, so an unrecognized failure must not read as "dead."
    """
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    ERROR_INVALID_PARAMETER = 87  # OpenProcess: no process with this pid
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    # ctypes.GetLastError() calls the real Win32 GetLastError() directly; it
    # works regardless of whether this DLL was loaded with use_last_error=True
    # (windll.kernel32 is not), unlike ctypes.get_last_error().
    return None if ctypes.GetLastError() == ERROR_INVALID_PARAMETER else False


class LockReadStatus(Enum):
    VALID = "VALID"
    EMPTY = "EMPTY"
    MALFORMED = "MALFORMED"
    NOT_PRESENT = "NOT_PRESENT"


class PidStatus(Enum):
    WRAPPER = "WRAPPER"          # alive AND cmdline contains JSC_WRAPPER_CMDLINE_MARKER
    NOT_WRAPPER = "NOT_WRAPPER"  # not alive OR cmdline confirmed not-JSC
    UNKNOWN = "UNKNOWN"          # alive but cmdline inspection unavailable (Codex-R5-P1-3)


class LockReadResult(NamedTuple):
    status: LockReadStatus
    state: dict | None  # None for non-VALID statuses


class LockConflictError(Exception):
    """Raised when another live wrapper holds the lock."""


class LockOwnershipError(Exception):
    """Raised when a release/heartbeat is attempted by a non-owner."""


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


def _seconds_since_iso(iso: str) -> float:
    """Returns seconds since the ISO timestamp; large positive number on parse fail."""
    try:
        if iso.endswith("Z"):
            iso = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        return time.time() - dt.timestamp()
    except (ValueError, TypeError):
        return float("inf")  # treat unparseable as ancient


def _try_read_lock(lock_path: Path) -> LockReadResult:
    """Tagged read: returns (status, state)."""
    if not lock_path.exists():
        return LockReadResult(LockReadStatus.NOT_PRESENT, None)
    try:
        content = lock_path.read_text(encoding="utf-8")
    except OSError:
        return LockReadResult(LockReadStatus.MALFORMED, None)
    if not content.strip():
        return LockReadResult(LockReadStatus.EMPTY, None)
    try:
        state = json.loads(content)
    except json.JSONDecodeError:
        return LockReadResult(LockReadStatus.MALFORMED, None)
    if not isinstance(state, dict):
        return LockReadResult(LockReadStatus.MALFORMED, None)
    required = (
        "owner_token", "owner_pid", "owner_hostname",
        "heartbeat_at_iso", "acquired_at_iso",
    )
    if not all(k in state for k in required):
        return LockReadResult(LockReadStatus.MALFORMED, None)
    # Field-TYPE validation (org-lock-malformed-crash, full-repo TAA 2026-05-31).
    # Presence alone was checked; a JSON-valid lock with wrong-typed fields (e.g.
    # owner_pid as a string, or heartbeat_at_iso as null) passed this gate and
    # then crashed downstream — os.kill(state["owner_pid"], 0) raises TypeError on
    # a non-int, and _seconds_since_iso(state["heartbeat_at_iso"]) raises
    # AttributeError on a non-str (only ValueError/TypeError were caught there).
    # Treat any type mismatch as MALFORMED → fail-closed to the mtime-threshold
    # steal path, same as garbage JSON.
    if not isinstance(state["owner_pid"], int) or isinstance(state["owner_pid"], bool):
        return LockReadResult(LockReadStatus.MALFORMED, None)
    for str_field in ("owner_token", "owner_hostname", "heartbeat_at_iso", "acquired_at_iso"):
        if not isinstance(state[str_field], str):
            return LockReadResult(LockReadStatus.MALFORMED, None)
    return LockReadResult(LockReadStatus.VALID, state)


def _check_pid_is_jsc_wrapper(pid: int) -> PidStatus:
    """Tristate PID validation per Codex-R5-P1-3.

    Returns:
        WRAPPER: pid is alive AND cmdline contains JSC_WRAPPER_CMDLINE_MARKER
        NOT_WRAPPER: pid is NOT alive (process died) OR cmdline confirms it's
                     a different process (PID reuse)
        UNKNOWN: pid is alive but cmdline inspection failed (PermissionError,
                 timeout, etc.) — caller should treat as live
    """
    # First: liveness check (kill -0). os.kill(pid, 0) is POSIX only: signal 0
    # on Windows raises OSError [WinError 87] "The parameter is incorrect"
    # rather than performing a liveness-only check.
    if sys.platform == "win32":
        alive = _win_pid_alive(pid)
        if alive is None:
            return PidStatus.NOT_WRAPPER  # process is dead
        if not alive:
            return PidStatus.UNKNOWN  # exists, but identity cannot be established
    else:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return PidStatus.NOT_WRAPPER  # process is dead
        except PermissionError:
            return PidStatus.UNKNOWN  # exists, but identity cannot be established

    # Second: cmdline inspection. Method depends on OS.
    if sys.platform == "win32":
        # No dependency-free, fast equivalent of reading /proc/{pid}/cmdline
        # or `ps -p <pid> -o command=` on Windows (WMIC is deprecated and
        # PowerShell's Get-CimInstance is slow enough to undermine the point
        # of a lock-staleness check). Never guess: the same UNKNOWN outcome
        # already used for PermissionError/timeout elsewhere in this
        # function is the safe default here too, and the caller already
        # treats it as "still live."
        return PidStatus.UNKNOWN
    if sys.platform == "linux":
        cmdline_path = Path(f"/proc/{pid}/cmdline")
        try:
            cmdline = cmdline_path.read_bytes().decode("utf-8", errors="replace")
            if any(marker in cmdline for marker in WRAPPER_CMDLINE_MARKERS):
                return PidStatus.WRAPPER
            return PidStatus.NOT_WRAPPER
        except (OSError, FileNotFoundError):
            return PidStatus.UNKNOWN  # /proc inaccessible
    else:
        # macOS / BSD: ps -p <pid> -o command=
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True, text=True,
                timeout=PS_CHECK_TIMEOUT_SECONDS,
            )
            if result.returncode != 0:
                # ps couldn't find process (it died between kill -0 and ps) → not wrapper
                return PidStatus.NOT_WRAPPER
            if any(marker in result.stdout for marker in WRAPPER_CMDLINE_MARKERS):
                return PidStatus.WRAPPER
            return PidStatus.NOT_WRAPPER
        except (subprocess.TimeoutExpired, PermissionError, FileNotFoundError, OSError):
            # Codex-R5-P1-3: empirically reproduced PermissionError on Darwin.
            # Return UNKNOWN, NOT False — caller treats as live until hard threshold.
            return PidStatus.UNKNOWN


def _is_stale(lock_path: Path, read_result: LockReadResult) -> bool:
    """Decide whether the lock is safely stealable.

    Stale if:
      - EMPTY/MALFORMED with mtime > HARD_ABSOLUTE_THRESHOLD ago
      - NOT_PRESENT (vacuously)
      - VALID cross-host with heartbeat > HARD_ABSOLUTE_THRESHOLD
      - VALID with heartbeat > STALE_THRESHOLD AND PID is NOT_WRAPPER (same host)

    NOT stale (caller waits / blocks) if:
      - VALID with heartbeat fresh (< STALE_THRESHOLD)
      - VALID same-host with a live or UNKNOWN PID even if the heartbeat is delayed
        on same host (Codex-R5-P1-3 fix — fail-closed instead of fail-open)
      - VALID different-host within HARD_ABSOLUTE (cross-host conservative)
    """
    status, state = read_result

    if status == LockReadStatus.NOT_PRESENT:
        return True

    if status in (LockReadStatus.EMPTY, LockReadStatus.MALFORMED):
        try:
            mtime = lock_path.stat().st_mtime
        except FileNotFoundError:
            return True
        return (time.time() - mtime) > LOCK_HARD_ABSOLUTE_THRESHOLD_SECONDS

    # status == VALID
    assert state is not None
    heartbeat_age = _seconds_since_iso(state["heartbeat_at_iso"])

    # Fresh heartbeat → live
    if heartbeat_age < LOCK_STALE_THRESHOLD_SECONDS:
        return False

    # Gray zone: STALE_THRESHOLD < heartbeat_age <= HARD_ABSOLUTE
    if state.get("owner_hostname") == socket.gethostname():
        # Same host: try PID check
        pid_status = _check_pid_is_jsc_wrapper(state["owner_pid"])
        if pid_status == PidStatus.NOT_WRAPPER:
            return True   # process died OR PID reused by non-wrapper → safe steal
        # A local live/unknown owner is not made dead by a long operation.
        # Wrappers refresh their lease, but a delayed refresh must not allow
        # another write while that owner is still running.
        return False
    else:
        # Different host: this local-file lease has only its heartbeat evidence.
        return heartbeat_age > LOCK_HARD_ABSOLUTE_THRESHOLD_SECONDS


def acquire_lock(
    org_id_short: str,
    org_alias: str,
    snapshot_id: str,
    operation_type: str,
    session_id: str | None = None,
) -> dict:
    """Acquire the per-org lock via O_CREAT|O_EXCL CAS.

    Returns the lock_state dict on success.
    Raises LockConflictError if another live wrapper holds the lock.
    Steals stale locks (audit-logged).
    """
    org_d = bundle.org_dir(org_id_short, org_alias)
    lock_path = org_d / ".org_lock.json"

    # Check existing lock first; steal if stale
    read_result = _try_read_lock(lock_path)
    if read_result.status != LockReadStatus.NOT_PRESENT:
        if _is_stale(lock_path, read_result):
            # Codex-R6-P1-3 fix: ABA race protection. Re-read immediately before
            # unlink to verify the lock state hasn't been replaced by a fresh
            # live lock between our stale-decision and our unlink. Compare the
            # owner_token (or status if VALID→other status) — if changed, abort
            # the steal and let the caller retry the whole acquire flow.
            recheck = _try_read_lock(lock_path)
            if recheck.status != read_result.status:
                # File state changed (e.g., went from EMPTY to VALID after a
                # fresh acquisition); refuse to delete blindly.
                raise LockConflictError(
                    f"ABA race detected: lock state changed from "
                    f"{read_result.status.value} to {recheck.status.value} "
                    f"between stale-check and steal. Retry acquire."
                )
            if (read_result.status == LockReadStatus.VALID
                and recheck.status == LockReadStatus.VALID):
                # Both VALID — verify it's the SAME stale lock by owner_token
                # AND acquired_at_iso (strict ABA defense).
                stale_state = read_result.state or {}
                recheck_state = recheck.state or {}
                if (stale_state.get("owner_token") != recheck_state.get("owner_token")
                    or stale_state.get("acquired_at_iso") != recheck_state.get("acquired_at_iso")):
                    raise LockConflictError(
                        f"ABA race detected: stale lock owner_token "
                        f"{stale_state.get('owner_token', '?')[:8]}... was replaced "
                        f"by fresh owner_token {recheck_state.get('owner_token', '?')[:8]}... "
                        f"between stale-check and steal. Retry acquire."
                    )
            _audit_steal(lock_path, read_result)
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass  # concurrent stealer won the race; that's OK
        else:
            state = read_result.state or {}
            raise LockConflictError(
                f"Org locked by token={state.get('owner_token', '?')[:8] if state.get('owner_token') else '?'}... "
                f"(snapshot={state.get('snapshot_id', '?')}, "
                f"operation={state.get('operation_type', '?')}, "
                f"acquired_at={state.get('acquired_at_iso', '?')}, "
                f"heartbeat_at={state.get('heartbeat_at_iso', '?')}). "
                "A live or unverifiable local owner retains its lease. Retry after "
                "it finishes; a confirmed crashed owner becomes recoverable once stale."
            )

    # Real CAS: O_CREAT|O_EXCL
    owner_token = str(uuid.uuid4())
    lock_state = {
        "schema_version": 1,
        "owner_token": owner_token,
        "owner_pid": os.getpid(),
        "owner_hostname": socket.gethostname(),
        "owner_session_id": session_id,
        "snapshot_id": snapshot_id,
        "operation_type": operation_type,
        "acquired_at_iso": _iso_now(),
        "heartbeat_at_iso": _iso_now(),
    }

    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        # Race: another process acquired between our check and create
        raise LockConflictError(
            f"Lock acquired by another process between read and CAS create at {lock_path}"
        )
    try:
        os.write(fd, json.dumps(lock_state).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)

    return lock_state


def heartbeat(
    org_id_short: str,
    org_alias: str,
    owner_token: str,
    *, lock_path: Path | None = None,
) -> None:
    """Update heartbeat_at_iso. Verify caller still owns the lock.

    Raises LockOwnershipError if caller is no longer the owner (lock was
    stolen or released).
    """
    if lock_path is None:
        lock_path = bundle.org_dir(org_id_short, org_alias) / ".org_lock.json"
    read_result = _try_read_lock(lock_path)
    if read_result.status != LockReadStatus.VALID:
        raise LockOwnershipError(
            f"Lock no longer valid: status={read_result.status.value}"
        )
    state = read_result.state
    assert state is not None
    if state.get("owner_token") != owner_token:
        raise LockOwnershipError(
            f"Lock owned by different token (caller {owner_token[:8]}..., "
            f"current {state.get('owner_token', '?')[:8]}...)"
        )
    state["heartbeat_at_iso"] = _iso_now()
    bundle.atomic_write_json(lock_path, state, mode=0o600)


def release(org_id_short: str, org_alias: str, owner_token: str, *, lock_path: Path | None = None) -> bool:
    """Verify ownership, then delete lock. Returns True if released."""
    if lock_path is None:
        lock_path = bundle.org_dir(org_id_short, org_alias) / ".org_lock.json"
    read_result = _try_read_lock(lock_path)
    if read_result.status != LockReadStatus.VALID:
        return False
    state = read_result.state
    if state and state.get("owner_token") == owner_token:
        try:
            lock_path.unlink()
            return True
        except FileNotFoundError:
            return False
    return False


def _audit_steal(lock_path: Path, read_result: LockReadResult) -> None:
    """Record stale-lock replacement locally without tokens or prior lock contents."""
    from jsc_common.workspace import state_dir
    try:
        audit_dir = state_dir("audit-logs")
        audit_dir.mkdir(parents=True, exist_ok=True)
        record = {"event": "revert_lock_steal", "at": _iso_now(), "lock_name": lock_path.name, "org_directory": lock_path.parent.name, "prior_status": read_result.status.value}
        fd = os.open(audit_dir / "revert-lock-events.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            output.write(json.dumps(record, sort_keys=True) + "\n")
    except (OSError, ValueError):
        print("warning: stale-lock audit could not be recorded in the selected client workspace", file=sys.stderr)
