# Validation for alpha 11: September 23, 2026

Alpha 11 is a development build with no package-index release. It changes only
de-identified mode (`src/torque/gate.py`), `torque doctor`'s hook check, one demo
walkthrough and documentation. The [alpha 10 record](validation-alpha10.md) still
describes Windows CI and the Windows limits; nothing here changes them.

## What prompted it

Four AI models reviewed alpha 10 independently. Driving the gate with hook events
against a scratch build-only workspace, they found these routes allowed:

- `cd project && cat ../clients/...`, `cat <clients/...`, `cat c*/...`;
- a filesystem MCP tool reading a `clients/` path;
- `git grep --untracked` and `--no-index`;
- `ai_access: null` (resolved to full) and a nested `workspace.json` of `{}`;
- a `Write` to the installed `gate.py`, and `pip uninstall` or a force-reinstall of Torque;
- a hook interpreter without Torque exiting 1, which Claude Code does not treat as a block.

Each has a regression test in `tests/test_gate_alpha11.py`, committed before the fix.
114 of that file's 153 tests failed against the alpha 10 gate.

## Results

- **Offline suite (macOS, Python 3.12, local):** 1276 pytest tests and 154 subtests
  pass, and the 12 standalone fixture suites complete. No live org or provider call.
- **CI:** `Validate Torque` on the pull request, all 9 cells (Ubuntu, macOS and
  Windows, each on Python 3.10, 3.12 and 3.14). The run id is recorded on the pull
  request. The doctor probe tests are skipped on Windows; the gate and shim tests run.

## Review scope

The fixes were written against the consolidated round-1 findings and have not yet
been re-reviewed. A security re-review is planned before merge. Until then, treat
the list in [de-identified mode](ai-access.md) as the claim to check.

## Known remaining limits

De-identified mode is pattern matching on recognized tool calls, not a sandbox.
Not covered: scripts the assistant writes and runs, `python -c` code, commands
built at run time (`cd "$DIR"`, `eval`), network tools using the user's own
credentials, MCP connectors whose client data is not a path under `clients/`
(mail, drive, chat), and a session started outside the workspace folder. There is
no org allowlist and no metadata-only mode: build-only blocks every org, and
`full` removes the guard. See [de-identified mode](ai-access.md) for the full list.
