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

    No reload needed: storage.memory_root() reads TORQUE_WORKSPACE/
    JSC_MEMORY_DIR from os.environ fresh on every call (its own docstring:
    "Resolve storage on every call so sequential clients never share
    state"), so monkeypatch.setenv() alone is picked up immediately.
    A prior version of this fixture called importlib.reload(jsc_memory.
    storage) "to pick up the env override" - unnecessary given the above,
    and reload() re-executes the module's top-level code, rebinding names
    like the Lesson dataclass to brand-new class/type objects each time.
    Removed as the prime suspect for an intermittent, windows-latest +
    Python 3.12 only test_capture.py flake (a different specific test each
    run, always "0 candidates found") that did not reproduce on 3.10/3.14.
    """
    monkeypatch.setenv("JSC_MEMORY_DIR", str(tmp_path))
    return tmp_path
