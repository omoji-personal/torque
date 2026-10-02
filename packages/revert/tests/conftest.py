import pytest


@pytest.fixture(autouse=True)
def isolated_org_locks(tmp_path, monkeypatch):
    monkeypatch.setenv("TORQUE_ORG_LOCK_DIR", str(tmp_path / "account-org-locks"))
