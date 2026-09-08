"""Call-time storage scope shared by the preserved JSC implementations.

The Torque launcher sets TORQUE_WORKSPACE to one selected client directory.
Direct package callers can select the same scope explicitly. No auth is stored
here and importing a module never creates directories.
"""
from __future__ import annotations

import os
from pathlib import Path


def workspace_root() -> Path:
    value = os.environ.get("TORQUE_WORKSPACE") or os.environ.get("JSC_ROOT")
    if not value:
        raise ValueError("Select a Torque workspace and client, or set TORQUE_WORKSPACE to a private client directory.")
    return Path(value).expanduser().resolve()


def state_dir(name: str, *, legacy_env: str | None = None) -> Path:
    """Return client-private state, ignoring stale legacy paths in Torque scope."""
    if not os.environ.get("TORQUE_WORKSPACE") and legacy_env and os.environ.get(legacy_env):
        return Path(os.environ[legacy_env]).expanduser().resolve()
    if not name or any(part in ("", ".", "..") for part in name.split("/")) or name.startswith("/"):
        raise ValueError("state name must be a relative path without traversal")
    root = workspace_root()
    destination = (root / "state" / name).resolve()
    if not destination.is_relative_to(root):
        raise ValueError("State path escapes the selected client workspace")
    return destination


def private_config(name: str, *, env: str | None = None) -> Path:
    if env and os.environ.get(env):
        return Path(os.environ[env]).expanduser().resolve()
    root = workspace_root()
    destination = (root / "config" / name).resolve()
    if not destination.is_relative_to(root):
        raise ValueError("Config path escapes the selected client workspace")
    return destination
