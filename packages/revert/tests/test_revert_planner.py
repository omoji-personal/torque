"""test_revert_planner.py — SEC-9 null-restore regression coverage.

Audit 2026-05-30 SEC-9: data_record_update revert silently dropped before-row
fields whose value was None, so a field the forward op changed null->value was
left at its new value on revert — yet revert_capabilities classified the op
REVERTIBLE_TRUE with no caveat (an overpromise). The fix surfaces the gap as a
side_effects_warning rather than emitting an unsafe '#N/A' blank token to
`sf data update record --values` (the sf CLI would write the literal string and
corrupt the field). These tests lock in: (a) value->value updates still revert
fully, (b) null before-values are still skipped (not corrupted), and (c) the
capability now carries the honest null-restore caveat.
"""
from pathlib import Path

from jsc_revert import revert_planner as rp
from jsc_revert import revert_capabilities as rc


def _manifest(before_row):
    return {
        "operation_type": "data_record_update",
        "snapshot_id": "snapX",
        "org": {"alias": "sf-x"},
        "payload": {
            "object_api_name": "Contact",
            "record_id": "003xx",
            "before_row": before_row,
            "fields_updated": [field for field in before_row if field != "Id"],
        },
    }


def test_value_to_value_fully_rendered():
    cmd = rp.build_revert_command(
        _manifest({"Id": "003xx", "FirstName": "Jane", "LastName": "Doe"}), None
    )
    vals = cmd[cmd.index("--values") + 1]
    assert "FirstName=Jane" in vals
    assert "LastName=Doe" in vals
    # No unsafe blank token is ever emitted.
    assert "#N/A" not in vals


def test_null_before_value_is_skipped_not_corrupted():
    # Forward op changed MiddleName null -> 'Q'; before-image is None.
    # SEC-9: the field is intentionally NOT emitted (and never as '#N/A'),
    # so the revert cannot corrupt it with a literal blank token.
    cmd = rp.build_revert_command(
        _manifest({"Id": "003xx", "FirstName": "Jane", "MiddleName": None}), None
    )
    vals = cmd[cmd.index("--values") + 1]
    assert "FirstName=Jane" in vals
    assert "MiddleName" not in vals
    assert "#N/A" not in vals


def test_all_null_before_row_returns_none():
    # If every restorable field was null, there is nothing to update.
    cmd = rp.build_revert_command(
        _manifest({"Id": "003xx", "MiddleName": None, "Suffix": None}), None
    )
    assert cmd is None


def test_capability_carries_null_restore_caveat():
    cap = rc.compute_revertibility("data_record_update", _manifest({"Id": "003xx", "FirstName": "Jane", "MiddleName": None})["payload"])
    # A partial null restore is explicitly best-effort, not full restoration.
    assert cap["automatic_revertible"] == "best-effort"
    # ...but the overpromise is gone: an explicit null-restore caveat is present.
    assert cap["side_effects_warning"]  # no longer empty (SEC-9 honesty)
    assert any("null" in w.lower() for w in cap["side_effects_warning"])


def _split_values(cmd):
    """sf --values is space-separated key=value pairs, with single-quoted values
    that contain spaces. Re-tokenize the way a shell/sf would."""
    import shlex
    vals = cmd[cmd.index("--values") + 1]
    return dict(p.split("=", 1) for p in shlex.split(vals))


def test_values_with_spaces_are_quoted(self=None):
    # REVERT-VALUES-AMBIGUOUS (TAA 2026-05-31): a before-value containing a space
    # must be single-quoted so the sf --values parser sees ONE pair, not two.
    cmd = rp.build_revert_command(
        _manifest({"Id": "003xx", "FirstName": "Jane", "Description": "lead = hot prospect"}), None
    )
    vals = cmd[cmd.index("--values") + 1]
    assert "Description='lead = hot prospect'" in vals, vals
    # Re-tokenizing yields exactly the right field values (no corruption / split).
    parsed = _split_values(cmd)
    assert parsed["Description"] == "lead = hot prospect"
    assert parsed["FirstName"] == "Jane"


def test_value_with_embedded_quote_bails_to_none():
    # A value containing a single quote is not safely representable in the flat
    # --values format → planner returns None (manual recovery), never a corrupt
    # token.
    cmd = rp.build_revert_command(
        _manifest({"Id": "003xx", "LastName": "O'Brien"}), None
    )
    assert cmd is None


def test_empty_string_value_is_quoted_not_dropped():
    # An empty-string before-value (distinct from None) must round-trip as ''.
    cmd = rp.build_revert_command(
        _manifest({"Id": "003xx", "FirstName": "Jane", "MiddleName": ""}), None
    )
    parsed = _split_values(cmd)
    assert parsed["MiddleName"] == ""
    assert parsed["FirstName"] == "Jane"


# ── B4: soft-delete undelete planner case ─────────────────────────────────


def _delete_manifest(delete_mode, *, before_row=None, record_id="003xx0000099AAA"):
    """A data_record_delete snapshot manifest (the revert TARGET)."""
    return {
        "operation_type": "data_record_delete",
        "snapshot_id": "snapDel",
        "org": {"alias": "sf-x"},
        "payload": {
            "object_api_name": "Contact",
            "record_id": record_id,
            "before_row": before_row,
            "delete_mode": delete_mode,
        },
    }


def test_soft_delete_builds_undelete_command():
    m = _delete_manifest(
        "soft",
        before_row={"Id": "003xx0000099AAA", "FirstName": "Jane", "LastName": "Doe"},
    )
    cmd = rp.build_revert_command(m, None)
    assert cmd is not None
    # Both a sibling console script and the same-interpreter module fallback
    # must reach the dedicated undelete command (no arbitrary PATH lookup).
    offset = 3 if cmd[1:3] == ["-m", "jsc_revert.cli"] else 1
    if offset == 1:
        assert Path(cmd[0]).name == "jsc"
    else:
        import sys
        assert cmd[0] == sys.executable
    assert cmd[offset:offset + 2] == ["data", "undelete"]
    assert "--sobject" in cmd and cmd[cmd.index("--sobject") + 1] == "Contact"
    assert "--record-id" in cmd and cmd[cmd.index("--record-id") + 1] == "003xx0000099AAA"
    # Carries the revert-chain args (mirrors deploy_metadata).
    assert "--parent-snapshot-id" in cmd
    assert cmd[cmd.index("--parent-snapshot-id") + 1] == "snapDel"
    assert "--operation-type" in cmd and cmd[cmd.index("--operation-type") + 1] == "revert"
    assert "--invoking-intent" in cmd
    assert "revert-of snapDel" in cmd[cmd.index("--invoking-intent") + 1]


def test_undelete_apex_syntax():
    # Guards S-3: ALL ROWS must be INSIDE the SOQL brackets and the id must be
    # a bind var (:rid), never string-interpolated into the SOQL.
    from jsc_revert.wrappers import data_undelete as du
    apex = du.build_undelete_apex("Contact", "003xx0000099AAA")
    # `ALL ROWS` sits inside the bracketed SOQL.
    bracket = apex[apex.index("["):apex.index("]") + 1]
    assert "ALL ROWS" in bracket
    # Bind var present; the id is NOT interpolated into the SOQL itself.
    assert ":rid" in bracket
    assert "WHERE Id = :rid" in apex
    assert "WHERE Id = '003xx0000099AAA'" not in apex
    # The id is bound via a separate Apex Id variable assignment.
    assert "Id rid = '003xx0000099AAA';" in apex


def test_undelete_apex_rejects_invalid_inputs():
    from jsc_revert.wrappers import data_undelete as du
    import pytest as _pytest
    with _pytest.raises(ValueError):
        du.build_undelete_apex("Contact; DROP", "003xx0000099AAA")
    with _pytest.raises(ValueError):
        du.build_undelete_apex("Contact", "not-an-id")


def test_hard_delete_still_none():
    m = _delete_manifest(
        "hard",
        before_row={"Id": "003xx0000099AAA", "FirstName": "Jane"},
    )
    assert rp.build_revert_command(m, None) is None


def test_compute_revertibility_agrees_with_planner():
    # automatic_revertible truthy IFF build_revert_command non-None, for soft AND hard.
    from jsc_revert import revert_capabilities as rcap

    soft = _delete_manifest(
        "soft", before_row={"Id": "003xx0000099AAA", "FirstName": "Jane"}
    )
    hard = _delete_manifest(
        "hard", before_row={"Id": "003xx0000099AAA", "FirstName": "Jane"}
    )

    soft_cap = rcap.compute_revertibility("data_record_delete", soft["payload"])
    hard_cap = rcap.compute_revertibility("data_record_delete", hard["payload"])

    soft_cmd = rp.build_revert_command(soft, None)
    hard_cmd = rp.build_revert_command(hard, None)

    assert bool(soft_cap["automatic_revertible"]) == (soft_cmd is not None)
    assert bool(hard_cap["automatic_revertible"]) == (hard_cmd is not None)
    # Concretely: soft is revertible (best-effort), hard is not.
    assert soft_cmd is not None and bool(soft_cap["automatic_revertible"]) is True
    assert hard_cmd is None and bool(hard_cap["automatic_revertible"]) is False
