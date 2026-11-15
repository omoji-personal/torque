r"""Windows forms that name a file on this machine without its drive path: an administrative share
(\\localhost\C$\...), a share this machine offers, and the device paths (\\?\C:\..., \\?\UNC\..., a volume name).
Alpha 22 and 23 let a build-only session read a client file through each of them, and looked any network path up
over the network, which could hold the gate for seconds. Found on 2026-11-15."""
from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import time

import pytest

from torque import gate, workspace as ws

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows path forms")


@pytest.mark.parametrize("raw, expected", [
    ("\\\\?\\C:\\w\\clients\\a", "C:\\w\\clients\\a"),
    ("//?/c:/w/clients/a", "C:/w/clients/a"),
    ("\\\\.\\D:\\w", "D:\\w"),
    ("\\\\?\\C:", "C:/"),
    ("\\\\?\\UNC\\localhost\\c$\\w\\clients", "C:\\w\\clients"),
    ("//host/D$/w/clients", "D:/w/clients"),
    ("\\\\127.0.0.1\\c$", "C:/"),
    ("//other-host/share/w/clients", "//other-host/share/w/clients"),
    ("\\\\?\\Volume{0000}\\w", "\\\\?\\Volume{0000}\\w"),
    ("// a comment", "// a comment"),
    ("//cdn.example.com/lib.js", "//cdn.example.com/lib.js"),
    ("C:/w/clients", "C:/w/clients"),
])
def test_device_paths_and_administrative_shares_are_read_as_their_drive_path(raw, expected, monkeypatch):
    monkeypatch.setattr(gate, "_local_shares", lambda: {})
    assert gate._drive_form(raw) == expected


def test_a_share_this_machine_offers_is_read_as_its_folder(monkeypatch):
    monkeypatch.setattr(gate, "_local_shares", lambda: {"work": "C:\\Work"})
    assert gate._drive_form("\\\\localhost\\Work\\w\\clients") == "C:/Work\\w\\clients"
    assert gate._drive_form("//ANY-HOST/work") == "C:/Work/"
    assert gate._drive_form("//localhost/other/w") == "//localhost/other/w"


@pytest.fixture
def root(tmp_path):
    base = tmp_path / "w"
    ws.init_workspace(base, "Synthetic")
    ws.add_client(base, "Acme")
    (base / "clients" / "acme" / "notes.md").write_text("x", encoding="utf-8")
    (base / "project").mkdir(exist_ok=True)
    return Path(os.path.realpath(base))


def forms(path: Path) -> list[str]:
    """path written as each form that reaches it from this machine."""
    drive, rest = path.drive[0], path.as_posix()[2:]
    host = os.environ.get("COMPUTERNAME", "localhost")
    return [f"//localhost/{drive}$" + rest, f"\\\\localhost\\{drive.lower()}$" + rest.replace("/", "\\"),
            f"//127.0.0.1/{drive}$" + rest, f"//{host}/{drive}$" + rest, f"//?/{drive}:" + rest,
            f"\\\\?\\{drive}:" + rest.replace("/", "\\"), f"\\\\?\\UNC\\localhost\\{drive}$" + rest.replace("/", "\\")]


def refused(root, tool, args, cwd=None):
    allowed, reason = gate.decide(tool, args, root, "build-only", cwd or root / "project")
    return not allowed and reason.startswith("Build-only mode")


@windows_only
def test_build_only_refuses_a_client_file_named_through_a_share_or_a_device_path(root):
    for path in forms(root / "clients" / "acme" / "notes.md"):
        assert refused(root, "Bash", {"command": f"cat '{path}'"}), path
        assert refused(root, "Read", {"file_path": path}), path
        assert refused(root, "PowerShell", {"command": f"Get-Content '{path}'"}), path
    for path in forms(root):
        assert refused(root, "Bash", {"command": f"cd '{path}' && cat clients/acme/notes.md"}), path
        assert refused(root, "Bash", {"command": f"cat '{path}'/${{A}}clients/acme/notes.md"}), path
        assert refused(root, "Bash", {"command": f"grep -r secret '{path}'"}), path


@windows_only
def test_build_only_refuses_a_client_file_named_through_a_share_this_machine_offers(root, monkeypatch):
    monkeypatch.setattr(gate, "_local_shares", lambda: {"tq": str(root.parent)})
    path = "//localhost/tq/" + root.name + "/clients/acme/notes.md"
    assert refused(root, "Bash", {"command": f"cat {path}"})
    assert refused(root, "Read", {"file_path": path.replace("/", "\\")})


@windows_only
def test_build_only_refuses_a_client_file_named_by_its_volume(root):
    import ctypes
    buffer = ctypes.create_unicode_buffer(64)
    if not ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(root.drive + "\\", buffer, 64):
        pytest.skip("no volume name for this drive")
    path = buffer.value + str(root / "clients" / "acme" / "notes.md")[3:]
    assert refused(root, "Read", {"file_path": path})
    assert refused(root, "Bash", {"command": f"cat '{path}'"})


@windows_only
def test_ordinary_work_on_this_machine_through_a_share_still_passes(root):
    for path in forms(root / "project"):
        allowed, reason = gate.decide("Bash", {"command": f"ls '{path}'"}, root, "build-only", root / "project")
        assert allowed, (path, reason)


@windows_only
def test_a_network_path_is_never_looked_up_or_listed(root, monkeypatch):
    touched = []
    real_realpath, real_isdir, real_iglob = os.path.realpath, os.path.isdir, gate.glob.iglob

    def realpath(path, *a, **k):
        touched.append(("realpath", str(path)))
        return real_realpath(path, *a, **k)

    def isdir(path):
        touched.append(("isdir", str(path)))
        return real_isdir(path)

    def iglob(pattern, *a, **k):
        touched.append(("glob", pattern))
        return real_iglob(pattern, *a, **k)

    monkeypatch.setattr(gate.os.path, "realpath", realpath)
    monkeypatch.setattr(gate.os.path, "isdir", isdir)
    monkeypatch.setattr(gate.glob, "iglob", iglob)
    monkeypatch.setattr(gate, "_local_shares", lambda: {})
    calls = [("Bash", {"command": "cat //no-such-host-x9/share/x.md"}),
             ("Bash", {"command": "ls //no-such-host-x9/share/*.md"}),
             ("Read", {"file_path": "\\\\no-such-host-x9\\share\\x.md"}),
             ("Bash", {"command": "cd //no-such-host-x9/share && cat x.md"})]
    for tool, args in calls:
        start = time.monotonic()
        allowed, reason = gate.decide(tool, args, root, "build-only", root / "project")
        assert allowed and time.monotonic() - start < 1.0, (args, reason)
    network = [(kind, raw) for kind, raw in touched if raw.replace("\\", "/").startswith("//")]
    assert network == [], network


@pytest.fixture
def full_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    base = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(base, "Acme")
    (Path(base) / "clients" / "acme" / "consent.json").write_text("{}", encoding="utf-8")
    ws.set_ai_access(base, "full")
    return Path(os.path.realpath(base))


def full_denied(root, tool, tool_input):
    event = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "cwd": str(root)}
    with redirect_stderr(io.StringIO()):
        return gate.evaluate(json.dumps(event)) != 0


@windows_only
def test_full_mode_keeps_a_record_named_through_a_share_or_a_device_path(full_root):
    for path in forms(full_root / "clients" / "acme" / "consent.json"):
        assert full_denied(full_root, "Bash", {"command": f"rm '{path}'"}), path
        assert full_denied(full_root, "Write", {"file_path": path, "content": "{}"}), path
