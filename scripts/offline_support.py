"""Child-process guard for the trusted offline suite, loaded through sitecustomize.

This prevents accidental live calls; it is not a sandbox for hostile test code.
No network destination except numeric loopback is allowed, including DNS/UDP.
"""
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys


BASE = Path(__file__).resolve().parent
BINS = BASE.parent / "bin"
LIVE = {"sf", "sfdx", "gemini", "claude", "codex", "curl", "wget", "ssh"}
SOURCE_TOOLS = {"jsc": "jsc_revert.cli", "torque": "torque.cli"}
# A test that supplies its own fake tools marks their folder with this file.
FIXTURE_MARKER = ".offline-fixture-bin"


def _fixture_tool(path):
    return bool(path) and (Path(path).resolve().parent / FIXTURE_MARKER).is_file()


def tool_main():
    name = Path(sys.argv[0]).stem
    args = sys.argv[1:]
    if name == "offline_support":
        name, *args = args
    if name in LIVE:
        with open(os.environ["TORQUE_TEST_LIVE_SENTINEL"], "a", encoding="utf-8") as stream:
            stream.write(name + "\n")
        print(json.dumps({"status": 1, "name": "OfflineBackendUnavailable",
                          "message": "The offline fixture backend is unavailable; no live call was made."}))
        return 1
    if name in SOURCE_TOOLS:
        return subprocess.call([sys.executable, "-m", SOURCE_TOOLS[name], *args])
    local = json.loads((BASE / "tools.json").read_text(encoding="utf-8"))
    executable = sys.executable if name in ("python", "python3") else local[name]
    return subprocess.call([executable, *args])


def _loopback(host):
    if isinstance(host, bytes):
        host = host.decode("ascii")
    try:
        address = ipaddress.ip_address(host)
        return address.is_loopback or bool(getattr(address, "ipv4_mapped", None)
                                           and address.ipv4_mapped.is_loopback)
    except ValueError:
        return False


def install():
    local = json.loads((BASE / "tools.json").read_text(encoding="utf-8"))
    # In a Windows virtual environment sys.executable is a launcher; the interpreter it
    # starts (this same Python) is what a child bound to its parent has to start.
    allowed = {str(Path(path).resolve()) for path in [sys.executable, getattr(sys, "_base_executable", None)
                                                      or sys.executable, *local.values()]}
    popen = subprocess.Popen

    class OfflinePopen(popen):
        def __init__(self, args, *positional, **kwargs):
            if kwargs.get("shell"):
                kwargs["executable"] = kwargs.get("executable") or (
                    os.environ["COMSPEC"] if os.name == "nt" else "/bin/sh")
            if not kwargs.get("shell") and isinstance(args, (list, tuple)) and args:
                executable = os.fsdecode(kwargs.get("executable") or args[0])
                name = Path(executable).stem if os.name == "nt" else Path(executable).name
                environment = kwargs.get("env") if kwargs.get("env") is not None else os.environ
                found = executable if os.path.dirname(executable) else shutil.which(
                    executable, path=environment.get("PATH", ""))
                if _fixture_tool(found):
                    executable = found
                elif name in LIVE or name in SOURCE_TOOLS:
                    # Not shutil.which: on Windows it searches the CWD first.
                    target = BINS / (name + ".exe" if os.name == "nt" else name)
                    if target.is_file():
                        executable = str(target)
                elif found:
                    executable = found
                    # A nested launcher puts its own tool folder on PATH; its
                    # Windows launchers are not this guard's, so use ours.
                    folder = Path(found).resolve().parent
                    target = BINS / Path(found).name
                    if (folder != BINS and target.is_file()
                            and (folder.parent / "bootstrap" / "offline_support.py").is_file()):
                        executable = str(target)
                kwargs["executable"] = executable
                # Subprocess overrides must retain the guard for fresh Python
                # interpreters (e.g. a test supplying its own PYTHONPATH).
            child_env = dict(kwargs.get("env") if kwargs.get("env") is not None else os.environ)
            # The bootstrap copy must come first: a checkout's scripts/ on the
            # caller's path would otherwise shadow it without its tools.json.
            paths = [path for path in child_env.get("PYTHONPATH", "").split(os.pathsep)
                     if path and Path(path).resolve() != BASE]
            child_env["PYTHONPATH"] = os.pathsep.join([str(BASE), *paths])
            kwargs["env"] = child_env
            super().__init__(args, *positional, **kwargs)

    def audit(event, args):
        host = None
        if event in {"socket.connect", "socket.bind", "socket.sendto", "socket.sendmsg"}:
            sock, address = args[0], args[-1]
            if address is not None and sock.family in (socket.AF_INET, socket.AF_INET6):
                host = address[0]
        elif event in {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}:
            host = args[0]
        elif event == "socket.getnameinfo":
            host = args[0][0]
        if host is not None and not _loopback(host):
            raise PermissionError("Offline suite blocked external network access")
        if event in {"subprocess.Popen", "os.exec", "os.posix_spawn"}:
            executable = args[0]
            # Windows Popen's audit argument can be None for shell commands.
            if executable is None:
                raise PermissionError("Offline suite requires an explicit executable")
            resolved = Path(shutil.which(os.fsdecode(executable)) or os.fsdecode(executable)).resolve()
            if str(resolved) not in allowed and resolved.parent != BINS and not _fixture_tool(resolved):
                raise PermissionError("Offline suite blocked an unapproved executable")
        if event == "os.system":
            raise PermissionError("Offline suite requires subprocess with an explicit executable")

    subprocess.Popen = OfflinePopen
    sys.addaudithook(audit)


if __name__ == "__main__":
    raise SystemExit(tool_main())
