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
