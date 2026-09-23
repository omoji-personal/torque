import contextlib, io, re
import pytest
from torque import cli


@pytest.mark.parametrize("route", sorted(cli.PUBLIC_ROUTES))
def test_public_route_help_names_torque(route):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit):
        cli.main([route, "--help"])
    text = out.getvalue()
    assert text.startswith(f"usage: torque {route}")
    assert "jsc" not in text.lower()


@pytest.mark.parametrize("route", ["qa", "logs", "probes", "advisory", "meeting", "lesson"])
def test_delegate_help_names_torque(route):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit):
        cli.main([route, "--help"])
    assert out.getvalue().startswith(f"usage: torque {route}")


# Final review I6: the full usage block (continuation lines joined) for every
# public route and every recover verb, so a leaked delegate grammar such as
# `torque recover revert exec` fails here instead of only checking a prefix.
PUBLIC_USAGE = {
    ("deploy",): "usage: torque deploy [-h] --target-org TARGET_ORG [--metadata METADATA] "
                 "[--manifest MANIFEST] [--source-dir SOURCE_DIR] "
                 "[--pre-destructive-changes PRE_DESTRUCTIVE_CHANGES] [--dry-run] "
                 "[--parent-snapshot-id PARENT_SNAPSHOT_ID] [--invoking-intent INVOKING_INTENT] "
                 "[--operation-type OPERATION_TYPE]",
    ("data",): "usage: torque data [-h] {update,create,delete,undelete,upsert,import,bulk} ...",
    ("org",): "usage: torque org [-h] {assign} ...",
    ("recover",): "usage: torque recover [-h] {show,preview,run,discard} ...",
    ("recover", "show"): "usage: torque recover show [-h] --org ORG [--limit LIMIT]",
    ("recover", "preview"): "usage: torque recover preview [-h] --org ORG snapshot_id",
    ("recover", "run"): "usage: torque recover run [-h] --org ORG [--force] [--reason REASON] snapshot_id",
    ("recover", "discard"): "usage: torque recover discard [-h] --org ORG snapshot_id",
}


def _help(monkeypatch, *argv):
    monkeypatch.setenv("COLUMNS", "80")
    out = io.StringIO()
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit):
        cli.main([*argv, "--help"])
    return out.getvalue()


def test_every_public_route_has_an_expected_usage():
    assert {key[0] for key in PUBLIC_USAGE} == set(cli.PUBLIC_ROUTES)


@pytest.mark.parametrize("argv", sorted(PUBLIC_USAGE))
def test_public_route_full_usage(monkeypatch, argv):
    text = _help(monkeypatch, *argv)
    block = text.split("\n\n", 1)[0]
    assert " ".join(block.split()) == PUBLIC_USAGE[argv]
    assert "revert" not in block and "exec" not in block and "jsc" not in text.lower()
    lines = block.splitlines()
    prog = PUBLIC_USAGE[argv].split(" [", 1)[0]
    for continuation in lines[1:]:
        assert continuation.startswith(" " * (len(prog) + 1)) and continuation[len(prog) + 1] != " ", continuation


def test_recover_help_lists_run_not_exec(monkeypatch):
    text = _help(monkeypatch, "recover")
    assert "\n    run " in text and not re.search(r"\bexec\b", text)
