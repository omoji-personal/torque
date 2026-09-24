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

A security re-review of that first candidate (the fixes above) then drove the gate
the same way and found a regression and further routes:

- `cd -`, `cd ~-`, `cd "$OLDPWD"`, `cd "$(git rev-parse --show-toplevel)"` and
  `popd` before a relative path (the regression: alpha 10 blocked the `cd -` and
  `popd` forms);
- a `torque/` folder or `sitecustomize.py` written into the workspace, which the
  hook's Python imported before the installed gate;
- Claude Code's `Monitor` tool and a `PowerShell` tool, which run commands the
  gate did not scan, and any tool name the gate did not recognise;
- Git Bash drive paths on Windows (`/c/...`), found by reading the code;
- git's abbreviated flags, `git diff --no-index`, `diff -r`, ANSI-C quoting and
  zsh glob groups.

Each has a test in the same file, written before its fix; 104 of the new tests
failed against the first candidate.

A second review round of that revision found more ordinary routes, each now with
a test written first (60 failed before the fix):

- leaving the workspace with `cd ..`, after which later calls from the parent
  folder were not gated at all;
- redirections glued to a word (`cat<file`), search option values read as the
  pattern (`rg -g '*.md' secret` searched the whole root), `git grep -- PATTERN`,
  `time cd` and `env -C DIR`;
- `rm -rf .venv` and other removals of the environment holding the gate;
- `sf project convert` rooted at the workspace, and curl's `@file` forms;
- doctor reporting a verified hook when the matcher missed tools or
  `disableAllHooks` was set.

## Results

- **Offline suite (macOS, Python 3.14.7, local):** 1488 pytest tests and 154
  subtests pass (1 Windows-only test skipped), and the 12 standalone fixture suites
  complete. No live org or provider call.
- **CI:** `Validate Torque` on the pull request, all 9 cells (Ubuntu, macOS and
  Windows, each on Python 3.10, 3.12 and 3.14). The run id is recorded on the pull
  request. On Windows the doctor probe tests run the hook through Git Bash, as
Claude Code does, and the Git Bash drive-path tests run.

## Review scope

The first fixes were written against the consolidated round-1 findings. Alpha 11
then had a security review of that first candidate and two re-reviews of the fixes
that followed (the second review round above, then a scoped re-review of that
revision). The scoped re-review asked for one change: the gate blocked host tools a
build session uses. That change (commit add6fa5: `SendMessage`, `EnterWorktree` and
`ExitWorktree` allowed, `LSP` checked on its `filePath`) was merged with its own
tests and then spot-checked.

The spot-check found three gaps in that change: `EnterWorktree` could enter a folder
outside `.claude/worktrees/`, and the worktree copies it creates could hold
`clients/` files the gate did not guard; `LSP` operations such as `workspaceSymbol`
answer from the language server's whole index, which covers `clients/`; and
`SendMessage` can reach a peer session that is not gated. The gaps are fixed in
alpha 12, the last by documenting it as a limit, as the spot-check recommended. See
the [alpha 12 record](validation-alpha12.md).

## Known remaining limits

De-identified mode is pattern matching on recognized tool calls, not a sandbox.
Not covered: scripts the assistant writes and runs, `python -c` code, commands
built at run time (a path from command output, `eval`), network tools using the user's own
credentials, MCP connectors whose client data is not a path under `clients/`
(mail, drive, chat), and a session started outside the workspace folder. There is
no org allowlist and no metadata-only mode: build-only blocks every org, and
`full` removes the guard. See [de-identified mode](ai-access.md) for the full list.
