"""Windows behaviour the suite's UTF-8 environment and a plain interpreter hide:
redirected output and hook input in the ANSI code page, a virtual environment's
launcher between a parent and its child, and \\r\\n from a text-mode write."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from torque import workspace as ws

TEXT = "Arrow → done, “quoted” 日本"


def plain_environment(**extra):
    """The environment without the suite's forced UTF-8 mode."""
    env = {key: value for key, value in os.environ.items() if key not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    if os.name != "nt":
        env["PYTHONUTF8"] = "1"  # the cases below are about Windows' code page
    return {**env, **extra}


def test_redirected_output_keeps_text_outside_the_ansi_code_page(tmp_path):
    root = ws.init_workspace(tmp_path / "firm", "Example firm")
    ws.add_client(root, "Alpha")
    ws.add_session(root, "alpha", TEXT)
    for command in (["session", "list"], ["context"], ["context", "--json"], ["handoff"]):
        done = subprocess.run([sys.executable, "-m", "torque", *command, "--workspace", str(root), "--client", "alpha"],
                              capture_output=True, env=plain_environment(), timeout=120)
        assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
        assert TEXT in done.stdout.decode("utf-8"), command


def test_hook_reads_utf8_input_whatever_the_stream_encoding(tmp_path):
    event = {"tool_name": "Write", "cwd": str(tmp_path),
             "tool_input": {"file_path": str(tmp_path / "notes.md"), "content": TEXT}}
    # cp1252 has no character for byte 0x9D, which UTF-8 uses in the closing quote.
    done = subprocess.run([sys.executable, "-m", "torque.gate"], input=json.dumps(event, ensure_ascii=False).encode("utf-8"),
                          capture_output=True, env=plain_environment(PYTHONIOENCODING="cp1252"), cwd=tmp_path,
                          timeout=120)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")


def test_regenerated_adapters_keep_unix_line_endings(tmp_path):
    source = Path(__file__).resolve().parents[1] / "workflows"
    shutil.copytree(source, tmp_path / "workflows", ignore=shutil.ignore_patterns("__pycache__"))
    done = subprocess.run([sys.executable, str(tmp_path / "workflows" / "sync_adapters.py")], capture_output=True,
                          text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    written = sorted((tmp_path / ".claude" / "commands").glob("*.md"))
    assert written and not any(b"\r" in path.read_bytes() for path in written)
    # Byte for byte what the checkout holds, so the bundle check stays quiet on Windows.
    tracked = source.parent / ".claude" / "commands"
    assert all(path.read_bytes() == (tracked / path.name).read_bytes().replace(b"\r\n", b"\n") for path in written)


def test_recovery_child_starts_the_real_interpreter_in_a_windows_venv(monkeypatch):
    from jsc_revert import revert_executor
    command = [sys.executable, "-m", "jsc_revert.cli", "deploy"]
    monkeypatch.setattr(sys, "_base_executable", sys.executable + ".base", raising=False)
    env = {}
    assert revert_executor._direct_interpreter(command, env, windows=True) == [sys.executable + ".base", *command[1:]]
    assert env == {"__PYVENV_LAUNCHER__": sys.executable}
    for unchanged, windows in ((command, False), (["jsc", "deploy"], True)):
        env = {}
        assert revert_executor._direct_interpreter(unchanged, env, windows=windows) == unchanged and env == {}
    monkeypatch.setattr(sys, "_base_executable", sys.executable, raising=False)
    assert revert_executor._direct_interpreter(command, env, windows=True) == command and env == {}


def test_recovery_child_sees_this_process_as_its_parent():
    from jsc_revert import revert_executor
    env = dict(os.environ)
    command = revert_executor._direct_interpreter([sys.executable, "-c", "import os; print(os.getppid())"], env)
    done = subprocess.run(command, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert int(done.stdout) == os.getpid()
