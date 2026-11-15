"""N8: edits stop resolving file contents as paths, and the git index query runs once
per call. Deterministic checks (no wall-clock assertions). Synthetic only."""
import os
from pathlib import Path

import pytest

from torque import gate, gate_connected as gc, workspace as ws
from torque.presence import Presence

YES = lambda: Presence(True, "")
BODY = ("public class Gen {\n" + "    Integer total = 0; // clients/acme/secret and $(rm -rf x) and /a /b\n" * 200)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.delenv("TORQUE_CLIENT", raising=False)


# claude-A2a#4: the content of a Write/Edit/MultiEdit (and a Bash description) is text
# the tool writes, never a path, so workspace discovery does not resolve its words.
def test_touched_paths_skips_content_fields(home, tmp_path):
    cwd = tmp_path
    few = gate._touched_paths({"file_path": "project/Gen.cls", "content": BODY}, cwd)
    # Only the file_path resolves; the 200-line body is not walked word by word.
    assert len(few) == 1
    assert not any("secret" in p.as_posix() for p in few)
    edit = gate._touched_paths({"file_path": "project/Gen.cls", "old_string": BODY, "new_string": BODY}, cwd)
    assert len(edit) == 1
    multi = gate._touched_paths({"file_path": "project/Gen.cls",
                                 "edits": [{"old_string": BODY, "new_string": BODY} for _ in range(16)]}, cwd)
    assert len(multi) == 1
    # A Bash command's words are still resolved (the description is not).
    paths = gate._touched_paths({"command": "cat project/a.txt other/b.txt", "description": "clients/acme/x"}, cwd)
    names = {p.name for p in paths}
    assert "a.txt" in names and "b.txt" in names and not any("acme" in p.as_posix() for p in paths)


def test_path_strings_excludes_content_keys():
    got = list(gate._path_strings({"file_path": "a", "content": "b", "edits": [{"new_string": "c"}],
                                   "nested": {"new_source": "d", "command": "e"}}))
    assert "a" in got and "e" in got
    assert "b" not in got and "c" not in got and "d" not in got


# claude-A2a#4: a full-mode MultiEdit with large content is allowed (the base refused it
# after its 5 s budget ran out in workspace discovery resolving the edit bodies).
def test_full_mode_large_multiedit_is_allowed(home, tmp_path, monkeypatch, capsys):
    import io
    import json
    root = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(root, "Acme")
    (Path(root) / "project").mkdir(exist_ok=True)
    ws.set_ai_access(root, "full")
    root = Path(os.path.realpath(root))
    event = {"cwd": str(root), "tool_name": "MultiEdit",
             "tool_input": {"file_path": str(root / "project" / "Gen.cls"),
                            "edits": [{"old_string": BODY, "new_string": BODY.replace("0", str(i))} for i in range(16)]}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    assert gate.main() == 0, capsys.readouterr().err


# gemini-code#2: clients_index_count is memoized per call, so repeated passes over many
# guarded client folders spawn the git query once, not once per folder.
def test_clients_index_count_is_cached_per_call(home, tmp_path, monkeypatch):
    root = ws.init_workspace(tmp_path / "firm", "Firm")
    calls = []
    real = gate._git_run

    def counting(base, args):
        calls.append(tuple(args[:1]))
        return real(base, args)
    monkeypatch.setattr(gate, "_git_run", counting)
    with gate._call_budget():
        a = gate.clients_index_count(root)
        b = gate.clients_index_count(root)
        c = gate.clients_index_count(root)
    assert a == b == c
    # Without the cache this would be three queries (two git processes each).
    assert len(calls) <= 2


def test_connected_many_clients_does_not_exhaust_the_budget(home, tmp_path, monkeypatch):
    root = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(root, "Acme")
    for i in range(30):
        ws.add_client(root, f"Extra{i:02d}")
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    root = Path(os.path.realpath(root))
    import subprocess
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e.invalid", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e.invalid")
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, capture_output=True, env=env)
    calls = []
    real = gate._git_run
    monkeypatch.setattr(gate, "_git_run", lambda base, args: (calls.append(1), real(base, args))[1])
    with gate._call_budget():
        gc.decide_connected("Bash", {"command": "git status && git diff"}, root, root,
                            env={"TORQUE_CLIENT": "acme"}, permission_mode="default", session_id="s", tool_use_id="t")
    # One workspace, so one index query pair, not one per client folder (~64 on the base).
    assert len(calls) <= 4, len(calls)
