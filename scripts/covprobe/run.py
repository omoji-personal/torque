#!/usr/bin/env python3
"""Measure which lines of bin/ and hooks/ a validation profile never executes.

    python3 scripts/covprobe/run.py --profile static
    python3 scripts/covprobe/run.py --profile capability --target-org <alias>

WHY THIS EXISTS. On 2026-08-07 the bounded production window (`torque approve --session`)
was found to raise UnboundLocalError on its first executed line. Nothing in 120 checks
touched it, and nobody would have written a check for it, because writing the check
requires already suspecting the branch. Unreached code is the one defect class findable
with no hypothesis, which is what makes it worth measuring rather than guessing at.

HOW. The harness spawns most tools as subprocesses, so an in-process tracer sees almost
nothing. `sitecustomize.py` is injected on PYTHONPATH instead — Python imports it at
startup in EVERY process, which is the one hook that reaches all of them without editing
the code under test. It delegates to the real sitecustomize first: shadowing it silently
removed this machine's site-packages setup and turned two passing checks red, an
instrumentation artifact that would have been read as a finding.

The first run measured 1945/3550 lines (55%). Exercising one unreached region
(blast-radius `cascade`, 49 lines) produced a real defect immediately.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SCOPE = [ROOT / "bin", ROOT / "hooks"]


def main():
    ap = argparse.ArgumentParser(prog="covprobe")
    ap.add_argument("--profile", default="static")
    ap.add_argument("--target-org")
    ap.add_argument("--hits", default=str(HERE / "hits"),
                    help="where per-process line hits are written")
    ap.add_argument("--keep", action="store_true", help="do not clear previous hits first")
    a = ap.parse_args()

    hits = Path(a.hits)
    if hits.exists() and not a.keep:
        shutil.rmtree(hits)
    hits.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["TQ_COV_DIR"] = str(hits)
    env["TQ_COV_SCOPE"] = os.pathsep.join(str(p) for p in SCOPE)
    env["PYTHONPATH"] = str(HERE) + (os.pathsep + env["PYTHONPATH"]
                                     if env.get("PYTHONPATH") else "")

    cmd = [sys.executable, str(ROOT / "harness" / "validate.py"), "--profile", a.profile]
    if a.target_org:
        cmd += ["--target-org", a.target_org]

    print(f"tracing: {' '.join(cmd[1:])}", flush=True)
    r = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True)
    verdict = [l for l in (r.stdout or "").splitlines() if "verdict:" in l]
    print(verdict[-1].strip() if verdict else f"(no verdict; exit {r.returncode})", flush=True)

    # An instrumented run that goes red where the plain run is green means the probe changed
    # the thing it measures. Say so rather than reporting the coverage as if it were clean.
    reds = [l for l in (r.stdout or "").splitlines() if l.strip().startswith("✗")]
    if reds:
        print(f"\nWARNING: {len(reds)} check(s) FAILED under instrumentation. Compare against an "
              f"untraced run before trusting the numbers — coverage measured during a broken "
              f"run under-reports what the profile normally reaches.")
        for l in reds[:5]:
            print(f"  {l.strip()[:150]}")

    # Successive runs differ by a line or two: a few branches are timing- or environment-
    # dependent. Treat the number as a trend, not a fingerprint, and compare like for like.
    print(flush=True)
    subprocess.run([sys.executable, str(HERE / "report.py"), str(hits)], cwd=str(ROOT))


if __name__ == "__main__":
    main()
