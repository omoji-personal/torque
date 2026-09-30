"""CLI: initiatives use the same commands as clients."""
import contextlib
import io
import json

from torque import cli
from torque import workspace as ws


def invoke(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(args))
        except SystemExit as exc:  # argparse usage errors exit instead of returning
            code = exc.code
    return code, out.getvalue(), err.getvalue()


def test_initiative_commands(tmp_path):
    root = str(ws.init_workspace(tmp_path / "firm", "Synthetic firm"))
    ws.add_client(root, "Alpha")
    assert invoke("initiative", "add", "Plan", "--workspace", root, "--owner", "lead")[0] == 0
    code, out, _ = invoke("engagement", "list", "--workspace", root, "--json")
    assert code == 0 and {r["kind"] for r in json.loads(out)} == {"client", "initiative"}
    assert invoke("session", "add", "--workspace", root, "--initiative", "Plan", "--summary", "s",
                  "--status", "prepared")[0] == 0
    code, out, _ = invoke("context", "--workspace", root, "--initiative", "Plan", "--json")
    assert code == 0 and json.loads(out)["client"]["kind"] == "initiative"
    assert invoke("initiative", "set-state", "Plan", "paused", "--workspace", root, "--reason", "wait")[0] == 0
    code, out, _ = invoke("initiative", "show", "Plan", "--workspace", root, "--json")
    assert json.loads(out)["state"] == "paused"


def test_client_and_initiative_are_exclusive(tmp_path):
    root = str(ws.init_workspace(tmp_path / "firm", "Synthetic firm"))
    code, _, err = invoke("context", "--workspace", root, "--client", "A", "--initiative", "B")
    assert code == 2 and "not allowed with" in err


def test_client_commands_are_unchanged(tmp_path):
    root = str(ws.init_workspace(tmp_path / "firm", "Synthetic firm"))
    ws.add_client(root, "Alpha")
    code, out, _ = invoke("session", "add", "--workspace", root, "--client", "Alpha", "--summary", "s",
                          "--status", "prepared", "--json")
    assert code == 0 and json.loads(out)["client"] == "alpha"


def test_initiative_text_says_initiative_and_client_text_is_unchanged(tmp_path):
    root = str(ws.init_workspace(tmp_path / "firm", "Synthetic firm"))
    ws.add_client(root, "Alpha")
    assert invoke("initiative", "add", "Plan", "--workspace", root)[0] == 0
    _, out, _ = invoke("context", "--workspace", root, "--initiative", "Plan")
    assert "Initiative directory: " in out and "Client directory:" not in out
    _, out, _ = invoke("session", "list", "--workspace", root, "--initiative", "Plan")
    assert out == "No session entries for this initiative.\n"
    _, out, _ = invoke("context", "--workspace", root, "--client", "Alpha")
    assert "Client directory: " in out and "Initiative directory:" not in out
    _, out, _ = invoke("session", "list", "--workspace", root, "--client", "Alpha")
    assert out == "No session entries for this client.\n"


def test_review_date_must_be_an_iso_date(tmp_path):
    import pytest
    from torque import engagements as eng
    root = ws.init_workspace(tmp_path / "firm", "Synthetic firm")
    eng.add_initiative(root, "Plan")
    for bad in ("next week", "2026-13-01", "2026-9-1", "2026-09-30T10:00"):
        with pytest.raises(ws.WorkspaceError, match="review date"):
            eng.set_state(root, "Plan", "paused", reason="wait", review_date=bad)
    config = eng.set_state(root, "Plan", "paused", reason="wait", review_date="2026-10-15")
    assert config["history"][-1]["review_date"] == "2026-10-15"


def test_doctor_checks_initiatives_for_tracked_files(tmp_path):
    from subprocess import CompletedProcess
    from unittest.mock import patch
    root = str(ws.init_workspace(tmp_path / "firm", "Synthetic firm"))
    with patch.object(cli.subprocess, "run", return_value=CompletedProcess([], 0, "", "")) as process:
        invoke("doctor", "--workspace", root, "--json")
    commands = [c.args[0] for c in process.call_args_list if "ls-files" in c.args[0]]
    assert commands and "initiatives" in commands[-1]
