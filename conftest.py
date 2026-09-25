"""Shared pytest setup for the whole checkout (a16 ruling F36).

`torque launch` sets TORQUE_CLIENT, TORQUE_WORKSPACE and TORQUE_LAUNCH in this
process's environment before it execs the agent. A test that calls it with a fake
exec would otherwise leak them into every later test. This sits at the checkout
root, not in tests/, because a tests/conftest.py collides with
packages/memory/tests/conftest.py under --import-mode=importlib (see pyproject.toml)."""
import os

import pytest

SESSION_ENV = ("TORQUE_CLIENT", "TORQUE_WORKSPACE", "TORQUE_LAUNCH")


@pytest.fixture(autouse=True)
def _restore_torque_session_env():
    saved = {key: os.environ.get(key) for key in SESSION_ENV}
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
