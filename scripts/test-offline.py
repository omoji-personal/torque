#!/usr/bin/env python3
"""Run offline tests with temporary state and an unavailable external-tool stub."""
import os
import ast
import argparse
from pathlib import Path
import re
import subprocess
import sys
import tempfile


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
        bins = scratch / "bin"
        bins.mkdir()
        hitfile = scratch / "unexpected-live-cli.txt"
        for tool in ("sf", "sfdx", "gemini", "claude"):
            path = bins / tool
            path.write_text(
                "#!/usr/bin/env python3\nimport os,sys,json\n"
                "with open(os.environ['TORQUE_TEST_LIVE_SENTINEL'], 'a') as f:\n"
                "    f.write(os.path.basename(sys.argv[0]) + '\\n')\n"
                "print(json.dumps({'status':1,'name':'OfflineBackendUnavailable',"
                "'message':'The offline fixture backend is unavailable; no live call was made.'}))\n"
                "raise SystemExit(1)\n"
            , encoding="utf-8")
            path.chmod(0o755)
            if os.name == "nt":
                # subprocess.run([tool, ...]) with shell=False (the pattern used
                # throughout this codebase) resolves a bare name against PATH by
                # appending only .exe; it never tries PATHEXT's other extensions.
                # A .cmd sibling still helps any caller that resolves via
                # shutil.which() (which does honor PATHEXT) or names the tool
                # with its extension explicitly. Code that calls the bare name
                # directly already treats FileNotFoundError as "tool not
                # installed" (see get_sf_cli_version, _sf_result), so an
                # unresolved bare call still fails closed the same way.
                (bins / f"{tool}.cmd").write_text(
                    "@echo off\r\n"
                    f"echo {tool}>>\"%TORQUE_TEST_LIVE_SENTINEL%\"\r\n"
                    "echo {\"status\": 1, \"name\": \"OfflineBackendUnavailable\", "
                    "\"message\": \"The offline fixture backend is unavailable; "
                    "no live call was made.\"}\r\n"
                    "exit /b 1\r\n"
                , encoding="utf-8")
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("TORQUE_", "JSC_"))}
        env.update({"JSC_ROOT": str(scratch / "client"),
                    "TORQUE_TEST_LIVE_SENTINEL": str(hitfile),
                    "PYTHONPATH": os.pathsep.join(str(path) for path in source_paths(root)),
                    "PATH": str(bins) + os.pathsep + env.get("PATH", ""),
                    # The standalone harnesses below print fixture labels containing
                    # non-ASCII characters (e.g. "->" as U+2192). With stdout piped
                    # (capture_output=True) rather than a real console, Windows
                    # defaults Python's stdout/stderr encoding to the system
                    # codepage (e.g. cp1252), which can't encode them and raises
                    # UnicodeEncodeError. Force UTF-8 regardless of platform/locale.
                    "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        # JSC contains pytest tests and standalone fixture harnesses. The latter
        # report failures by process exit and must not be silently just imported.
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
        if hitfile.exists():
            from collections import Counter
            calls = Counter(hitfile.read_text(encoding="utf-8").splitlines())
            print(f"Failure-path requests handled by the unavailable offline stub: {dict(calls)}. No live tool was invoked.")
        return code


if __name__ == "__main__":
    raise SystemExit(main())
