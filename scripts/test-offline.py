#!/usr/bin/env python3
"""Run offline tests with temporary state and an unavailable external-tool stub."""
import os
import ast
import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


LIVE_TOOLS = ("sf", "sfdx", "gemini", "claude", "codex", "curl", "wget", "ssh")
LOCAL_TOOLS = ("git", "bash", "sh", "cmd", "ps", "rm", "chmod", "ln", "getfacl", "setfacl")


def offline_environment(root: Path, scratch: Path) -> dict[str, str]:
    """Build a clean environment, with only the local tools the suite needs.

    Windows CreateProcess searches for .exe, not .cmd. Use distlib's native
    launchers, and resolve child commands explicitly in offline_support too:
    Windows searches the application directory and CWD before PATH.
    """
    import json

    # Keep the stub installation separate from fixture state: connected mode
    # protects an sf installation's parent, so state cannot live under it.
    bins = scratch / "tools" / "bin"
    bins.mkdir(parents=True)
    support = scratch / "tools" / "bootstrap"
    support.mkdir()
    shutil.copyfile(Path(__file__).with_name("offline_support.py"), support / "offline_support.py")
    (support / "sitecustomize.py").write_text(
        "import os, traceback\ntry:\n    import offline_support\n    offline_support.install()\n"
        "except BaseException:\n    traceback.print_exc()\n    os._exit(2)\n", encoding="utf-8")
    local = {name: shutil.which(name) for name in LOCAL_TOOLS}
    local = {name: path for name, path in local.items() if path}
    outer_guard = sys.modules.get("offline_support")
    if outer_guard is not None:
        # Nested launcher regressions must use the original local executables,
        # not forward through a previous guard with a different allowlist.
        local = json.loads((outer_guard.BASE / "tools.json").read_text(encoding="utf-8"))
    (support / "tools.json").write_text(json.dumps(local), encoding="utf-8")
    names = (*LIVE_TOOLS, *local, "python", "python3", "jsc", "torque")
    if os.name == "nt":
        from distlib.scripts import ScriptMaker

        maker = ScriptMaker(None, str(bins))
        maker.executable = sys.executable
        maker.variants = {""}
        for name in names:
            maker.make(f"{name} = offline_support:tool_main")
    else:
        for name in names:
            path = bins / name
            if name in local:
                path.symlink_to(local[name])
                continue
            # A quoted interpreter path also supports a venv under a space.
            import shlex
            path.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable)
                            + " " + shlex.quote(str(support / "offline_support.py"))
                            + " " + shlex.quote(name) + ' "$@"\n', encoding="utf-8")
            path.chmod(0o755)
    # An allowlist avoids carrying provider tokens, proxy credentials, CLI
    # overrides, Python startup settings or authentication sockets into tests.
    keep = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "SYSTEMDRIVE",
            "LANG", "LC_ALL", "LC_CTYPE", "TZ"}
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    if os.name == "nt":
        env["PATHEXT"] = ".EXE"
    home = scratch / "home"
    for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME",
                 "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "TMP", "TEMP", "TMPDIR"):
        path = home if name in ("HOME", "USERPROFILE") else scratch / name.lower()
        path.mkdir(exist_ok=True)
        env[name] = str(path)
    env["HOMEDRIVE"], env["HOMEPATH"] = os.path.splitdrive(str(home))
    env.update({"JSC_ROOT": str(scratch / "client"),
                "TORQUE_TEST_LIVE_SENTINEL": str(scratch / "unexpected-live-cli.txt"),
                "TORQUE_TEST_PRIVATE_SCAN_STATUS": str(scratch / "private-scan.txt"),
                "PYTHONPATH": os.pathsep.join(str(path) for path in [support, *source_paths(root)]),
                "PATH": str(bins), "PYTHONNOUSERSITE": "1",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_ALLOW_PROTOCOL": "", "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "core.hooksPath",
                "GIT_CONFIG_VALUE_0": str(scratch / "no-hooks"),
                "GIT_CONFIG_KEY_1": "core.fsmonitor", "GIT_CONFIG_VALUE_1": "false"})
    if os.environ.get("TORQUE_PRIVATE_DENYLIST"):
        # Expand before replacing HOME, including when invoked outside the repo.
        env["TORQUE_PRIVATE_DENYLIST"] = str(Path(os.environ["TORQUE_PRIVATE_DENYLIST"]).expanduser().resolve())
    return env


def source_paths(root: Path) -> list[Path]:
    """Use this checkout in pytest and fresh child processes, never a stale install."""
    packages = sorted(path for path in (root / "packages").glob("*")
                      if path.is_dir() and any(path.glob("*/__init__.py")))
    return [root / "src", *packages]


def standalone_summary(stdout: str) -> tuple[bool, str]:
    """Require measured completion from the inherited executable harnesses.

    A zero exit alone can also mean a whole suite declined to run. These are the
    three actual summary formats emitted by the checked-in harnesses; per-case
    skips may coexist with completed measurements but are not whole-suite PASS.
    """
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        return False, "No fixture completion summary was returned"
    summary = lines[-1]
    if any(re.match(r"FAIL(?:ED)?\s*:", line) for line in lines):
        return False, "Fixture failures were reported despite the zero exit"
    counted = re.fullmatch(r".+ self-test PASSED \(([0-9]+) fixtures\)", summary)
    ratio = re.fullmatch(r".+ self-test PASSED: ([0-9]+)/([0-9]+) fixtures", summary)
    if counted and int(counted.group(1)) > 0:
        return True, summary
    if ratio and int(ratio.group(1)) > 0 and ratio.group(1) == ratio.group(2):
        return True, summary
    # The retained MCP capture harness predates counted summaries. Its explicit
    # PASS lines are the measurement record; accepting its banner alone is unsafe.
    if summary == "mcp_capture self-test PASSED" and any(line.startswith("PASS:") for line in lines):
        return True, summary
    return False, "No nonempty successful fixture summary: " + summary


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--pytest-only", action="store_true")
    options, pytest_args = parser.parse_known_args()
    with tempfile.TemporaryDirectory(prefix="torque-offline-") as temporary:
        scratch = Path(temporary)
        hitfile = scratch / "unexpected-live-cli.txt"
        env = offline_environment(root, scratch)
        # The carried packages contain pytest tests and standalone fixture harnesses.
        # The latter report failures by process exit and must not be silently just imported.
        standalone = []
        for path in sorted((root / "packages").glob("*/tests/test*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            collected = any((isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_"))
                            or (isinstance(n, ast.ClassDef) and (n.name.startswith("Test") or any(
                                (isinstance(base, ast.Attribute) and base.attr == "TestCase")
                                or (isinstance(base, ast.Name) and base.id == "TestCase")
                                for base in n.bases))) for n in tree.body)
            if not collected:
                standalone.append(path)
        ignores = [f"--ignore={p.relative_to(root).as_posix()}" for p in standalone]
        result = subprocess.run([sys.executable, "-m", "pytest", *ignores, *pytest_args], cwd=root, env=env)
        code = result.returncode
        # Pytest options such as '--maxfail 1' have non-option operands too.
        # Only an explicit opt-out or collection/help mode skips executable suites.
        inspection = any(arg in ("--collect-only", "--co", "--help", "-h", "--version")
                         for arg in pytest_args)
        if not options.pytest_only and not inspection:
            for path in standalone:
                # Forward slashes regardless of platform: this label is printed and
                # also matched by tests against a fixed reference string.
                label = path.relative_to(root).as_posix()
                try:
                    run = subprocess.run([sys.executable, str(path)], cwd=root, env=env,
                                         capture_output=True, text=True, timeout=180)
                except subprocess.TimeoutExpired:
                    print(f"INCOMPLETE {label}: executable fixture suite exceeded 180 seconds", file=sys.stderr)
                    code = 1
                    continue
                if run.returncode:
                    print(f"FAIL {label}\n{run.stdout}\n{run.stderr}", file=sys.stderr)
                    code = 1
                else:
                    complete, summary = standalone_summary(run.stdout)
                    if not complete:
                        print(f"INCOMPLETE {label}: {summary}\n{run.stdout}\n{run.stderr}", file=sys.stderr)
                        code = 1
                    else:
                        print(f"PASS {label}: {summary}")
        elif options.pytest_only:
            print("Selected pytest tests only; standalone fixture suites were not run.")
        scan = Path(env["TORQUE_TEST_PRIVATE_SCAN_STATUS"])
        status = scan.read_text(encoding="utf-8") if scan.exists() else (
            "NOT RUN (test not selected or did not complete)" if env.get("TORQUE_PRIVATE_DENYLIST")
            else "NOT RUN (TORQUE_PRIVATE_DENYLIST not configured)")
        print("Private denylist scan: " + status)
        if hitfile.exists():
            from collections import Counter
            calls = Counter(hitfile.read_text(encoding="utf-8").splitlines())
            print(f"Failure-path requests handled by the unavailable offline stub: {dict(calls)}. No live tool was invoked.")
        return code


if __name__ == "__main__":
    raise SystemExit(main())
