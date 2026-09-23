"""Shared pytest fixtures for jsc_memory tests."""
import os
import sys
from pathlib import Path

# Add packages/memory to sys.path so `import jsc_memory` works
_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))


import pytest


@pytest.fixture(autouse=True)
def isolated_memory_dir(tmp_path, monkeypatch):
    """Each test gets its own JSC_MEMORY_DIR.

    No reload needed: storage.memory_root() reads JSC_MEMORY_DIR from
    os.environ on every call, so monkeypatch.setenv() alone takes effect.
    """
    monkeypatch.setenv("JSC_MEMORY_DIR", str(tmp_path))
    return tmp_path
