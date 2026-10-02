import pytest

from jsc_revert import bundle, org_detect, org_sequence


def test_alias_and_client_changes_still_contend_for_same_org(tmp_path, monkeypatch):
    org = "00D000000000001"
    monkeypatch.setenv("TORQUE_WORKSPACE", str(tmp_path / "client-a"))
    first_snapshot = bundle.new_snapshot_dir(org, "first")[0]
    owner = org_sequence.acquire_lock(org, "first", "snapshot-a", "deploy_metadata")
    try:
        monkeypatch.setenv("TORQUE_WORKSPACE", str(tmp_path / "client-b"))
        second_snapshot = bundle.new_snapshot_dir(org, "second")[0]
        with pytest.raises(org_sequence.LockConflictError):
            org_sequence.acquire_lock(org_detect._pad_to_18(org), "second", "snapshot-b", "deploy_metadata")
        assert first_snapshot.parent != second_snapshot.parent
    finally:
        org_sequence.release(org, "first", owner["owner_token"])


@pytest.mark.parametrize("org", ["../outside", "alias", "00D000000000001AAA", "001000000000001"])
def test_org_lock_rejects_unvalidated_identity(org):
    with pytest.raises(ValueError):
        org_sequence.acquire_lock(org, "alias", "snapshot", "deploy_metadata")


def test_case_distinct_orgs_have_distinct_keys_even_on_windows():
    assert org_sequence.org_lock_path("00D00000000000a").name.lower() != org_sequence.org_lock_path("00D00000000000A").name.lower()
