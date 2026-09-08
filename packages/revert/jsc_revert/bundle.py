"""bundle.py — storage primitives for snapshot bundles.

Provides:
  - default + override path resolution (JSC_REVERT_DIR)
  - org-keyed directory structure (<org_id_short>-<alias>/<iso-ts>-<hash>/)
  - file-mode discipline (dir 0o700, file 0o600)
  - atomic writes via temp + os.replace
"""

from __future__ import annotations
from jsc_common.workspace import state_dir

import json
import os
import time
import uuid
from pathlib import Path


DEFAULT_REVERT_DIR = None  # compatibility symbol; defaults resolve at call time


def revert_dir() -> Path:
    """Resolve the JSC_REVERT_DIR root, creating it if missing (mode 0o700)."""
    base = state_dir("revert", legacy_env="JSC_REVERT_DIR")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    return base


def org_dir(org_id_short: str, org_alias: str) -> Path:
    """Per-org directory: <revert-dir>/<org_id_short>-<alias>/

    org_id_short is the first 15 chars of the 18-char org id; alias is the
    sf CLI alias. Both safe for filesystem (alphanumeric + hyphen).
    """
    safe_alias = "".join(c if c.isalnum() or c == "-" else "_" for c in org_alias)
    safe_short = "".join(c if c.isalnum() else "_" for c in org_id_short)
    d = revert_dir() / f"{safe_short}-{safe_alias}"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def new_snapshot_dir(org_id_short: str, org_alias: str, snapshot_id: str | None = None) -> tuple[Path, str]:
    """Create a fresh snapshot bundle dir. Returns (path, snapshot_id)."""
    if snapshot_id is None:
        snapshot_id = uuid.uuid4().hex[:16]
    iso_ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    short_hash = snapshot_id[:8]
    snap_dir = org_dir(org_id_short, org_alias) / f"{iso_ts}-{short_hash}"
    snap_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return snap_dir, snapshot_id


def atomic_write_text(path: Path, content: str, mode: int = 0o600) -> None:
    """Write `content` to `path` atomically: write to temp then os.replace.

    Per .claude/rules/lesson-capture.md atomic-write pattern.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Use tmp file in same dir so os.replace is atomic on same filesystem
    tmp_path = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    fd = os.open(str(tmp_path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, mode)
    try:
        os.write(fd, content.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp_path), str(path))


def atomic_write_json(path: Path, data: dict, mode: int = 0o600) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=False), mode=mode)
