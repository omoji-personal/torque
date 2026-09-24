# Validation through alpha 9 — September 22, 2026

Alpha 10 has its own record: [validation for alpha 10](validation-alpha10.md). It covers
Windows CI, build-only mode and the Windows limits that remain. Alpha 11 hardens
build-only mode: [validation for alpha 11](validation-alpha11.md). Alpha 12 closes the gaps a
spot-check of alpha 11 found: [validation for alpha 12](validation-alpha12.md). Alpha 13 closes
the routes a later review round found: [validation for alpha 13](validation-alpha13.md). Alpha 14
blocks over-long commands before any pattern runs: [validation for alpha 14](validation-alpha14.md).

Alpha 9 is a development build with no package-index release. It improves session continuity,
local diagnostics, source/private separation and the offline verification runner.
The [alpha 8 record](validation-alpha8.md) retains prior live and remote CI evidence;
those results do not qualify alpha 9's changed bytes.

## Reproduced problems and repairs

| Problem reproduced against alpha 8 | Alpha 9 behavior |
| --- | --- |
| Session evidence changed after recording without appearing stale in resumed context | Session reads rehash the original file and expose matching, changed, missing or unavailable evidence; recorded history remains intact |
| Malformed session evidence crashed a handoff with `KeyError`; malformed nested metadata could also crash rendering | Validate local records before rendering, identify the affected record and preserve it for repair |
| Doctor inspected only recent sessions and omitted change records | Check all selected-client sessions and changes; report evidence problems and identify the loaded package/interpreter |
| A CLI installed elsewhere allowed private initialization inside a Torque source tree | Inspect destination ancestors for Torque's source identity, including Git worktrees and extracted source distributions |
| The offline runner could test a stale installed core package | Set the checkout's package roots for pytest and child processes; a fresh-process regression supplies a deliberately stale import path |
| `--maxfail 1` silently omitted all executable fixture suites | Run those suites unless explicitly opting into `--pytest-only` or inspecting collection/help |

Session references still point to their original files; change checks capture a
private copy. Hashes establish byte consistency, not the truth of a supplied
claim. Missing/unavailable evidence leaves resumption usable. Malformed records
and cross-client references identify a local error without rewriting history.
Captured evidence checks both length and hash. Large files are hashed in chunks.

Human-readable reports display “not run”; JSON retains `not_run`. The version
increment and additive diagnostic fields do not migrate private records or change
org authorization. Updated session recipes can be adopted through the existing
workspace updater, which preserves customized files.

## Source verification

| Local runtime | Pytest tests | Subtests | Completed standalone suites |
| --- | ---: | ---: | ---: |
| macOS, Python 3.10.21 | 895 | 154 | 12 |
| macOS, Python 3.12.14 | 895 | 154 | 12 |
| macOS, Python 3.14.3 | 895 | 154 | 12 |

All three runs passed. These are the same tests exercised on three runtimes,
including 37 added regression cases. The Python 3.10/3.12 runs used fresh
virtual environments with no installed Torque package; the runner imported the
current checkout. Salesforce/provider executables were replaced by unavailable
stubs. The runs handled 16, 16 and 17 stubbed Salesforce failure paths respectively.
Five inherited optional source-mirror checks inside the QA executable harness
remain skipped; the suite's measured fixtures completed. No live org/provider
operation was made by these checks.

The 49 adapters, 64 bundled resources and 180 inherited provenance entries match.
Python 3.10 syntax and Git whitespace checks pass. Changed product code is in the
Torque layer; inherited runtime package bytes remain unchanged.

## Package verification

A clean wheel installation must be tested separately from source. The installed
smoke script runs from a temporary directory with `PYTHONPATH` removed and verifies:

- Nine delegate routes, six public routes, all 49 recipes and the offline demo.
- Private initialization, two-client/four-change continuation across fresh processes,
  reported failures, scoped context/handoff and preserved workflow customizations.
- Session evidence drift, complete selected-client diagnostics and the destination
  source/private boundary with an externally installed CLI.

Wheel/sdist surface checks reject private/generated content and require packaged
workflows. The local qualification report retains exact artifact hashes and logs;
these identify the checked build rather than an arbitrary later rebuild.

## Remaining evidence

The local qualification above included no new live Salesforce or browser execution,
remote CI run or publication. Later commit/push and remote CI results are separate
evidence tied to the exact published commit. Earlier live non-admin browser
cases remain unqualified for this build. Windows (offline, demo and gate paths
CI-tested from alpha 10; live Salesforce routes unqualified), outside-user onboarding,
multiple assistant hosts, existing delivery-stack integration and comparative
benchmarks also remain separate acceptance work. The local fixes do not establish
production readiness or a universal recovery capability.

## Reproduce

Install development dependencies into an isolated environment, then run:

```sh
python workflows/sync_adapters.py --check
python scripts/sync-workflows.py --check
python scripts/check-provenance.py
python scripts/test-offline.py -q
python -m build --outdir work/release-dist
python scripts/check-distribution.py work/release-dist/*.whl --sdist work/release-dist/*.tar.gz
python -m venv work/wheel-venv
work/wheel-venv/bin/python -m pip install work/release-dist/*.whl
work/wheel-venv/bin/python -m pip check
work/wheel-venv/bin/python scripts/smoke-installed.py --require-wheel
```

Use a fresh output directory to avoid selecting old artifacts. For focused
iteration, `python scripts/test-offline.py --pytest-only tests/test_record_resilience.py -q`
explicitly skips the executable suites. It is not a full qualification run.
