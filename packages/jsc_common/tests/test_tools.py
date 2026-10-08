"""Starting a tool that Windows installs as a batch file; fake tools only, no Salesforce call."""
import json
import os
import subprocess
import sys

import pytest

from jsc_common import tools

NPM_SHIM = ('@ECHO off\r\nGOTO start\r\n:find_dp0\r\nSET dp0=%~dp0\r\nEXIT /b\r\n:start\r\nSETLOCAL\r\n'
            'CALL :find_dp0\r\n\r\nIF EXIST "%dp0%\\node.exe" (\r\n  SET "_prog=%dp0%\\node.exe"\r\n) ELSE (\r\n'
            '  SET "_prog=node"\r\n  SET PATHEXT=%PATHEXT:;.JS;=;%\r\n)\r\n\r\n'
            'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%" --no-deprecation '
            '"%dp0%\\node_modules\\example\\bin\\run.js" %*\r\n')
# What an org alias, a query or a restored field value can hold.
AWKWARD = ["plain", "two words", "", "a&b", "a|b", "<in>", "^caret", "100%", "%PATH%", "!bang!", 'say "hi"',
           "trailing\\", 'back\\"slash', "(group)", "semi;colon,comma", "café 日本",
           "SELECT Id FROM Account WHERE Name = 'A & B \"C\" %x%' AND Amount > 5"]


def touch(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return path


def test_command_is_unchanged_off_windows_and_for_an_exe(tmp_path):
    argv = ["sf", "org", "list"]
    env = {"PATH": str(tmp_path)}
    touch(tmp_path / "sf.cmd", NPM_SHIM)
    assert tools.command(argv, env, windows=False) == (argv, {})
    assert tools.command(["missing-tool", "x"], env, windows=True) == (["missing-tool", "x"], {})
    touch(tmp_path / "later" / "sf.exe")
    both = {"PATH": os.pathsep.join([str(tmp_path), str(tmp_path / "later")])}
    # Windows starts an .exe by itself, wherever it is on PATH.
    assert tools.command(argv, both, windows=True) == (argv, {})


def test_npm_shim_starts_its_node_script_directly(tmp_path):
    touch(tmp_path / "bin" / "sf.cmd", NPM_SHIM)
    node = touch(tmp_path / "bin" / "node.exe")
    script = touch(tmp_path / "bin" / "node_modules" / "example" / "bin" / "run.js")
    line, extra = tools.command(["sf", "data", "query", "--query", "a & b"], {"PATH": str(tmp_path / "bin")},
                                windows=True)
    assert line == [str(node), "--no-deprecation", str(script), "data", "query", "--query", "a & b"]
    assert extra == {}
    # Without node.exe beside the shim, the one on PATH is used, as the shim does.
    node.unlink()
    other = touch(tmp_path / "node" / "node.exe")
    line, _ = tools.command(["sf", "x"], {"PATH": os.pathsep.join([str(tmp_path / "bin"), str(tmp_path / "node")])},
                            windows=True)
    assert line[0] == str(other)


def test_other_batch_file_runs_through_cmd_with_literal_arguments(tmp_path):
    batch = touch(tmp_path / "bin" / "sf.cmd", '@echo off\r\n"%~dp0..\\client\\node.exe" "%~dp0..\\client\\run" %*\r\n')
    line, extra = tools.command(["sf", "a&b", 'say "hi"', "100%", "plain", ""], {"PATH": str(tmp_path / "bin")},
                                windows=True)
    assert line == f'cmd.exe /e:ON /v:OFF /d /c ""{batch}" "a&b" "say ""hi""" "100%%cd:~,%" plain """'
    assert extra["executable"].lower().endswith("cmd.exe")
    with pytest.raises(OSError, match="line break"):
        tools.command(["sf", "first\nsecond"], {"PATH": str(tmp_path / "bin")}, windows=True)


def test_tool_is_never_taken_from_the_current_folder(tmp_path, monkeypatch):
    touch(tmp_path / "sf.cmd", NPM_SHIM)
    monkeypatch.chdir(tmp_path)
    for path in ("", ".", os.pathsep.join(["", "."])):
        assert tools.command(["sf", "x"], {"PATH": path}, windows=True) == (["sf", "x"], {})
        assert tools.command(["sf.cmd", "x"], {"PATH": path}, windows=True) == (["sf.cmd", "x"], {})


def test_run_passes_an_ordinary_command_on_unchanged(monkeypatch):
    seen = []
    monkeypatch.setattr(subprocess, "run", lambda *args, **options: seen.append((args, options)) or "done")
    argv = [sys.executable, "-c", "pass"]
    assert tools.run(argv, capture_output=True, timeout=5) == "done"
    assert seen == [((argv,), {"capture_output": True, "timeout": 5})]


def test_text_output_is_read_as_utf8_on_windows_unless_stated(monkeypatch):
    seen = {}
    monkeypatch.setattr(subprocess, "run", lambda *args, **options: seen.update(options))
    tools.run(["missing-tool"], text=True, env={"PATH": ""})
    # Elsewhere the locale already gives UTF-8; Windows would use the ANSI code page.
    assert seen.get("encoding") == ("utf-8" if os.name == "nt" else None)
    tools.run(["missing-tool"], text=True, encoding="cp1252", errors="strict", env={"PATH": ""})
    assert (seen["encoding"], seen["errors"]) == ("cp1252", "strict")
    seen.clear()
    tools.run(["missing-tool"], capture_output=True, env={"PATH": ""})
    assert "encoding" not in seen


@pytest.mark.skipif(os.name != "nt", reason="batch files are a Windows matter")
def test_batch_file_receives_every_argument_literally(tmp_path):
    echo = touch(tmp_path / "echo.py", "import json, sys\nsys.stdout.buffer.write(json.dumps(sys.argv[1:]).encode())\n")
    batch = touch(tmp_path / "tool dir" / "echo-args.cmd", f'@echo off\r\n"{sys.executable}" "{echo}" %*\r\n')
    done = tools.run([str(batch), *AWKWARD], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == AWKWARD
    # The same tool found by its bare name on PATH (the way `sf` is found).
    env = {**os.environ, "PATH": str(batch.parent) + os.pathsep + os.environ.get("PATH", "")}
    done = tools.run(["echo-args", "a&b", "%OS%"], capture_output=True, text=True, timeout=120, env=env)
    assert json.loads(done.stdout) == ["a&b", "%OS%"]
