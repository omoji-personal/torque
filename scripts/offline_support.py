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


def _stub_argv(name, local):
    """What a stub stands for, as a command: the trusted interpreter or local tool itself."""
    if name in LIVE:
        return [sys.executable, str(BASE / "offline_support.py"), name]
    if name in SOURCE_TOOLS:
        return [sys.executable, "-m", SOURCE_TOOLS[name]]
    if name in ("python", "python3"):
        return [sys.executable]
    return [local[name]] if name in local else None


def _shim_command(executable, local):
    """What a Windows .cmd stub shim stands for, or None for any other file.

    CreateProcess would run a shim through cmd.exe, which rereads %, ^, & and quotes
    in the arguments; starting what it stands for passes them as given.
    """
    if os.name != "nt" or Path(executable).suffix.lower() != ".cmd":
        return None
    folder = Path(executable).resolve().parent
    if folder == BINS or (folder.parent / "bootstrap" / "offline_support.py").is_file():
        return _stub_argv(Path(executable).stem.lower(), local)
    return None


def _stub_alias(name):
    """The stub shim for a bare Windows spelling (python.exe, GIT, Sf.cmd), or None.

    Windows names are case-insensitive and callers add .exe; the stubs are .cmd shims.
    """
    if os.name != "nt" or os.path.dirname(name) or Path(name).suffix.lower() not in ("", ".exe", ".cmd"):
        return None
    shim = BINS / (Path(name).stem.lower() + ".cmd")
    return str(shim) if shim.is_file() else None


def _patch_launch_helper(local):
    """Dispatch stubs before jsc_common.tools quotes a command for cmd.exe.

    That helper sees a .cmd stub on PATH, as it would a real npm shim, and builds a
    cmd.exe line it cannot give a line break. Real batch files keep the helper's quoting.
    """
    import importlib.abc
    import importlib.util

    class Finder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname != "jsc_common.tools":
                return None
            sys.meta_path.remove(self)
            try:
                spec = importlib.util.find_spec(fullname)
            finally:
                sys.meta_path.insert(0, self)
            if spec is None or spec.loader is None:
                return spec
            execute = spec.loader.exec_module

            def exec_module(module):
                execute(module)
                original = module.command

                def command(argv, env=None, *, windows=module.os.name == "nt"):
                    first = os.fspath(argv[0]) if argv else ""
                    found = first if os.path.dirname(first) else (shutil.which(
                        first, path=(os.environ if env is None else env).get("PATH", ""))
                        or (_stub_alias(first) if first.lower().endswith(".exe") else None))
                    stub = _shim_command(found, local) if found else None
                    if windows and stub:
                        # The command as given, as a list: the shape a real .exe gives, no cmd.exe line, so code
                        # that reads the call sees it unchanged. OfflinePopen resolves the shim and starts what it
                        # stands for.
                        return [first, *(os.fspath(a) for a in argv[1:])], {}
                    return original(argv, env, windows=windows)

                module.command = command

            spec.loader.exec_module = exec_module
            return spec

    sys.meta_path.insert(0, Finder())


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
                name = Path(executable).stem.lower() if os.name == "nt" else Path(executable).name
                environment = kwargs.get("env") if kwargs.get("env") is not None else os.environ
                found = executable if os.path.dirname(executable) else shutil.which(
                    executable, path=environment.get("PATH", ""))
                if _fixture_tool(found):
                    executable = found
                elif name in LIVE or name in SOURCE_TOOLS:
                    # Not shutil.which: on Windows it searches the CWD first.
                    target = BINS / (name + ".cmd" if os.name == "nt" else name)
                    if target.is_file():
                        executable = str(target)
                elif _stub_alias(executable) and (not found or Path(found).resolve().parent == BINS):
                    executable = _stub_alias(executable)
                elif found:
                    executable = found
                    # A nested launcher puts its own tool folder on PATH; its
                    # Windows launchers are not this guard's, so use ours.
                    folder = Path(found).resolve().parent
                    target = BINS / Path(found).name
                    if (folder != BINS and target.is_file()
                            and (folder.parent / "bootstrap" / "offline_support.py").is_file()):
                        executable = str(target)
                stub = _shim_command(executable, local)
                if stub:
                    args = [*stub, *args[1:]]
                    executable = stub[0]
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
    if os.name == "nt":
        _patch_launch_helper(local)
    sys.addaudithook(audit)


if __name__ == "__main__":
    raise SystemExit(tool_main())
