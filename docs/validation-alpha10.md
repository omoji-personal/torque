# Validation for alpha 10: September 23, 2026

Alpha 10 is a development build with no package-index release. It adds de-identified mode,
Windows in CI, four synthetic demo recipes, and `torque` naming on every forwarded route.
The [alpha 9 record](validation.md) keeps the earlier live and local evidence. Those results
do not qualify alpha 10's changed bytes.

## Source and CI verification

- **Offline suite (macOS, Python 3.12, local):** 1123 pytest tests and 154 subtests pass,
  and the 12 standalone fixture suites complete. Salesforce executables were replaced by
  unavailable stubs, and no live org or provider call was made.
- **Wheel and sdist:** the surface checks pass: 53 recipes and 42 legacy JSC command mappings,
  compared name by name with the table in `workflow-continuity.md`. A clean wheel
  installation passes the installed smoke script.
- **CI:** `Validate Torque` is green on all 9 cells (Ubuntu, macOS and Windows, each on
  Python 3.10, 3.12 and 3.14). Commit `e836d88` passed in run 35931089056 (push) and run
  35931091758 (pull request). The earlier release commit `de3ba0f` passed in run 35929620563.
- **Inherited packages:** Windows portability required changes to inherited package files:
  encoding, line endings, process liveness and file locking. `provenance.json` was
  refreshed to match. Unlike alpha 9, inherited runtime bytes are not unchanged.

## What Windows covers, and what it does not yet cover

CI on Windows qualifies the following:

- the offline workspace and context commands;
- the offline demo;
- the de-identified-mode gate, including a real hook subprocess.

The owner's Windows laptop run was skipped by decision. The winget, `py -3` and
`pip install -e .` steps in `installation.md` have not been run as written; CI uses
`setup-python`.

Not yet qualified on Windows:

- **Live Salesforce routes:** `torque deploy`, `data`, `org`, `recover`, `qa` and `logs`,
  and the `sf` calls of inherited packages. These packages call `sf` without a shell. Windows
  resolves only `sf.exe` that way, and the Salesforce CLI installs as `sf.cmd`, so these
  routes will likely report `sf` as missing. CI proves only that this failure is handled.
- **Revert lock:** a stale revert lock held by a reused process id is never taken over.
- **Skip-token validation:** the `msvcrt` lock gives up after about 10 seconds, and the
  resulting error is not caught in skip-token validation.
- **Consume lock:** the `.consume-lock` file is never removed.
- **Token hardening:** the mode and owner checks on tokens are off. The user identity comes
  from `USERNAME`.

## De-identified mode security review

The gate went through the following review rounds:

- **Initial review:** found direct bypasses. Bash reads of `clients/`, `python -m torque`, a
  session rewriting `workspace.json`, and org flags on local-looking `sf` subcommands all got
  through.
- **Three fix rounds:** closed those bypasses, then wrappers, grouping, substitutions,
  `$HOME` and symlinks, and a workspace marker that was too broad.
- **Windows fixes:** made `HOME` splicing and the hook interpreter safe.
- **Final whole-branch review:** found that Torque's own `jsc*` scripts, `python -m jsc_*` and
  `-mtorque` bypassed build-only mode entirely, and that MCP tools were outside the hook
  matcher. Both are now gated, and a test fails if a new console script is left unclassified.

Accepted limits are listed in [de-identified mode](ai-access.md). These include name-only MCP
matching, `git grep`, globs and arbitrary scripts. The gate is a best-effort guard, not a
sandbox.
