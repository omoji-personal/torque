"""Per-session event spool — capture-time scratchpad.

Per plan-v5: PostToolUse + PostToolUseFailure hooks append events to
${JSC_MEMORY_DIR}/spool/<session_id>.jsonl. Stop hook reads spool, walks
events, builds candidates, deletes spool. UserPromptSubmit hook appends
operator_correction events (with scrubbed summary, NOT raw content).

Atomic appends via append-mode open (POSIX guarantees small writes are atomic
on local filesystem).

Recovery: at posttool startup, scan for orphaned spools (no live session, > 1h
old). Per-spool atomic O_EXCL lease prevents concurrent recovery races.
"""

from __future__ import annotations

import json
import os
import pathlib
import time
from typing import Iterator, Optional

from . import storage


def spool_path_for_session(session_id: str) -> pathlib.Path:
    return storage.spool_dir() / f"{session_id}.jsonl"


def lease_path_for_spool(spool_path: pathlib.Path) -> pathlib.Path:
    return spool_path.with_suffix(spool_path.suffix + ".lease")


def append_event(session_id: str, event: dict) -> None:
    """Atomic append to per-session spool. Fail-open (silent on disk error)."""
    if not session_id:
        return
    storage.ensure_dirs()
    spool_path = spool_path_for_session(session_id)
    line = json.dumps(event, default=str) + "\n"
    try:
        # Append-mode write of a single line < PIPE_BUF is POSIX-atomic. Create the
        # spool 0o600 (and fchmod any pre-existing file) — it transiently holds raw
        # Bash commands before the Stop-hook scrubs them into L1 candidates (audit
        # 2026-06-09 LESSON-1; plain open() previously left it at the umask default).
        fd = os.open(str(spool_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            # os.fchmod does not exist on Windows; mode bits are POSIX-only there anyway.
            if os.name != "nt":
                try:
                    os.fchmod(fd, 0o600)
                except OSError:
                    pass
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except OSError:
        # Fail-open per privacy-and-logging rule
        pass


def read_events(session_id: str) -> list[dict]:
    """Read all events from a session spool. Returns [] if absent."""
    spool_path = spool_path_for_session(session_id)
    if not spool_path.exists():
        return []
    out = []
    try:
        with open(spool_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def delete_spool(session_id: str) -> None:
    """Delete spool + any lease. Best-effort."""
    spool_path = spool_path_for_session(session_id)
    lease = lease_path_for_spool(spool_path)
    for p in (spool_path, lease):
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass


def acquire_lease(spool_path: pathlib.Path) -> Optional[int]:
    """Atomic O_EXCL lease for orphan recovery. Returns fd if acquired, else None.

    Per plan-v5: race-free on POSIX local filesystem. May be non-atomic on
    NFS/SMB/some FUSE — operators with JSC_MEMORY_DIR on network share should
    redirect to a local path.
    """
    lease = lease_path_for_spool(spool_path)
    try:
        fd = os.open(str(lease), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, f"{os.getpid()}\n".encode())
        return fd
    except FileExistsError:
        return None
    except OSError:
        return None


def release_lease(spool_path: pathlib.Path, fd: Optional[int]) -> None:
    """Close + remove lease file. Idempotent."""
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass
    lease = lease_path_for_spool(spool_path)
    try:
        if lease.exists():
            lease.unlink()
    except OSError:
        pass


def scan_orphaned_spools(min_age_seconds: int = 3600) -> Iterator[pathlib.Path]:
    """Yield spool paths older than min_age_seconds (default 1h).

    Caller is responsible for acquire_lease before processing each.
    """
    storage.ensure_dirs()
    cutoff = time.time() - min_age_seconds
    for p in storage.spool_dir().glob("*.jsonl"):
        try:
            if p.stat().st_mtime < cutoff:
                yield p
        except OSError:
            continue


def quarantine_spool(spool_path: pathlib.Path) -> Optional[pathlib.Path]:
    """Move a stale/unrecoverable spool to _quarantine/ subdirectory."""
    storage.ensure_dirs()
    quarantine = storage.spool_dir() / "_quarantine"
    quarantine.mkdir(parents=True, exist_ok=True)
    try:
        target = quarantine / spool_path.name
        if target.exists():
            # Append timestamp to avoid collision
            target = quarantine / f"{spool_path.stem}-{int(time.time())}.jsonl"
        spool_path.rename(target)
        return target
    except OSError:
        return None


def age_out_quarantine(max_age_days: int = 7) -> int:
    """Delete quarantined spools older than max_age_days. Returns count deleted."""
    storage.ensure_dirs()
    quarantine = storage.spool_dir() / "_quarantine"
    if not quarantine.exists():
        return 0
    cutoff = time.time() - (max_age_days * 86400)
    deleted = 0
    for p in quarantine.glob("*.jsonl"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                deleted += 1
        except OSError:
            continue
    return deleted


def enforce_spool_dir_cap(max_files: int = 50) -> int:
    """Cap spool dir at max_files; drop oldest first. Returns count dropped."""
    storage.ensure_dirs()
    spools = sorted(storage.spool_dir().glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if len(spools) <= max_files:
        return 0
    drop_count = len(spools) - max_files
    for p in spools[:drop_count]:
        try:
            p.unlink()
        except OSError:
            continue
    return drop_count
