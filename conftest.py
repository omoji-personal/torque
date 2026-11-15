"""Shared pytest setup for the whole checkout (a16 ruling F36).

`torque launch` sets TORQUE_CLIENT, TORQUE_WORKSPACE and TORQUE_LAUNCH in this
process's environment before it execs the agent. A test that calls it with a fake
exec would otherwise leak them into every later test. This sits at the checkout
root, not in tests/, because a tests/conftest.py collides with
packages/memory/tests/conftest.py under --import-mode=importlib (see pyproject.toml)."""
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile

import pytest

SESSION_ENV = ("TORQUE_CLIENT", "TORQUE_WORKSPACE", "TORQUE_LAUNCH")
CHECKOUT = Path(__file__).resolve().parent
# Outside scripts/test-offline.py, Python would import an installed copy of Torque
# instead of this checkout, and the tests would inherit the shell: credentials, a
# selected TORQUE_WORKSPACE, the real sf, claude and codex. So a plain pytest session
# first gives itself the runner's offline environment: this checkout first on the
# import path, stubs for live tools on PATH, the runner's environment allowlist with a
# temporary home and client folder, and its guard on network access and executables,
# in this process and every child. The standalone fixture suites stay runner-only.
OFFLINE_RUNNER = """the offline environment for this pytest session could not be set up ({problem}).
Run the tests through the offline runner instead:
  python scripts/test-offline.py -q                                   everything CI runs
  python scripts/test-offline.py --pytest-only -q tests/test_cli.py   a selection; pytest options pass through"""
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


def _offline_runner_active() -> bool:
    # Its startup guard is loaded in every interpreter it starts, with its sentinel set.
    return "offline_support" in sys.modules and bool(os.environ.get("TORQUE_TEST_LIVE_SENTINEL"))


def _ours(name: str) -> bool:
    top = name.split(".")[0]
    return top in ("torque", "meeting_processor") or top.startswith("jsc_")


def _go_offline() -> tuple[Path, str]:
    """Apply scripts/test-offline.py's environment to this session: its scratch folder,
    and the runner's note when that folder is deep for Windows' path limit."""
    stale = sorted(name for name, module in list(sys.modules.items())
                   if _ours(name) and getattr(module, "__file__", None)
                   and not Path(module.__file__).resolve().is_relative_to(CHECKOUT))
    if stale:
        raise RuntimeError("already imported from outside this checkout: " + ", ".join(stale[:3]))
    spec = importlib.util.spec_from_file_location("torque_offline_runner", CHECKOUT / "scripts" / "test-offline.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    scratch = Path(tempfile.mkdtemp(prefix="torque-offline-"))
    try:
        env = runner.offline_environment(CHECKOUT, scratch)
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    os.environ.clear()
    os.environ.update(env)
    tempfile.tempdir = None  # read TMP again
    support = scratch / "tools" / "bootstrap"
    sys.path[:0] = [str(support), *map(str, runner.source_paths(CHECKOUT))]
    if "offline_support" not in sys.modules:  # a guard already running stays the only one
        import offline_support
        offline_support.install()
    return scratch, runner.path_limit_note(scratch, runner.windows_path_limit_applies())


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    # First, so the tmp_path factory sees the short basetemp the runner also uses.
    if _offline_runner_active():
        return
    try:
        scratch, config._torque_path_note = _go_offline()
    except Exception as exc:  # fail closed: no session outside the offline environment
        raise pytest.UsageError(OFFLINE_RUNNER.format(problem=exc)) from exc
    config._torque_offline_scratch = scratch
    if config.option.basetemp is None:
        config.option.basetemp = str(scratch / "p")


def pytest_terminal_summary(terminalreporter, config):
    scratch = getattr(config, "_torque_offline_scratch", None)
    if scratch is None:
        return
    terminalreporter.write_line("Ran in the offline environment of scripts/test-offline.py; its standalone fixture "
                                "suites run only through that runner.")
    note = getattr(config, "_torque_path_note", "")
    if note:
        terminalreporter.write_line(note)
    hits = scratch / "unexpected-live-cli.txt"
    if hits.exists():
        from collections import Counter
        calls = dict(Counter(hits.read_text(encoding="utf-8").splitlines()))
        terminalreporter.write_line(f"Failure-path requests handled by the unavailable offline stub: {calls}. "
                                    "No live tool was invoked.")


def pytest_unconfigure(config):
    scratch = getattr(config, "_torque_offline_scratch", None)
    if scratch is not None:
        shutil.rmtree(scratch, ignore_errors=True)


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
