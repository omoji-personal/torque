"""Shared pytest setup for the whole checkout (a16 ruling F36).

`torque launch` sets TORQUE_CLIENT, TORQUE_WORKSPACE and TORQUE_LAUNCH in this
process's environment before it execs the agent. A test that calls it with a fake
exec would otherwise leak them into every later test. This sits at the checkout
root, not in tests/, because a tests/conftest.py collides with
packages/memory/tests/conftest.py under --import-mode=importlib (see pyproject.toml)."""
import os
from pathlib import Path

import pytest

SESSION_ENV = ("TORQUE_CLIENT", "TORQUE_WORKSPACE", "TORQUE_LAUNCH")
CHECKOUT = Path(__file__).resolve().parent
# Windows creates a symbolic link only with Developer Mode or an elevated prompt
# and answers WinError 1314 otherwise. Torque creates none itself, so a test that
# builds one to see how Torque treats links cannot run there. It is reported as
# skipped, with this reason, only when the test or its helper made the link: the
# same error from Torque's own code stays a failure.
NO_LINK_PRIVILEGE = 1314
NO_LINK_REASON = "creating a symbolic link needs Developer Mode or an elevated prompt on Windows"


def _link_refused_to_test(excinfo) -> bool:
    error = excinfo.value
    if os.name != "nt" or not isinstance(error, OSError) or getattr(error, "winerror", None) != NO_LINK_PRIVILEGE:
        return False
    for entry in reversed(excinfo.traceback):
        try:
            parts = Path(str(entry.path)).resolve().relative_to(CHECKOUT).parts
        except (OSError, ValueError):
            continue  # the standard library, between the caller and the system call
        return "tests" in parts or parts[-1] == "conftest.py"
    return False


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.failed and call.excinfo is not None and _link_refused_to_test(call.excinfo):
        report.outcome = "skipped"
        report.longrepr = (str(item.path), item.location[1] or 0, "Skipped: " + NO_LINK_REASON)


@pytest.fixture(autouse=True)
def _restore_torque_session_env():
    saved = {key: os.environ.get(key) for key in SESSION_ENV}
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
