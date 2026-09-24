# Validation for alpha 12: September 23, 2026

Alpha 12 is a development build with no package-index release. It changes only
de-identified mode (`src/torque/gate.py`) and documentation. The
[alpha 11 record](validation-alpha11.md) and the [alpha 10 record](validation-alpha10.md)
still describe the earlier fixes, Windows CI and the Windows limits.

## What prompted it

A spot-check of alpha 11's last change (commit add6fa5, which let host tools a
build session uses through the gate) drove the gate with hook events against a
scratch build-only workspace and found:

- `EnterWorktree` with a `path` into `clients/` or anywhere outside
  `.claude/worktrees/` was allowed;
- `EnterWorktree` with a `name` creates a copy of the tree under
  `.claude/worktrees/`, and `Read`, `Grep` and `Glob` of that copy's `clients/`
  were allowed; a `Write` of `.worktreeinclude` naming `clients/` was allowed too,
  so the session could arrange the copy itself;
- `LSP` operations that answer from the whole workspace (`workspaceSymbol`,
  `findReferences`, the call hierarchy) were allowed, as were a call with no
  `filePath` and a `file://` URI into `clients/`;
- `SendMessage` to a peer session that is not gated (documented, not blocked).

A review of the same release found that git working-tree commands got past the
self-protection and client-folder checks when the workspace root is a git
repository: `git clean -fdx` deleted the hook's environment, `.claude/` and
`clients/`, and `git stash --all && git stash show -p --include-untracked` printed a
client file.

Each has a regression test in `tests/test_gate_alpha12.py`, committed before the
fix. 90 of that file's 118 tests failed against the alpha 11 gate. One alpha 11
test that allowed `EnterWorktree` with a `name` in a workspace that is not a git
repository now expects a worktree path instead.

## Results

- **Offline suite (macOS, Python 3.12.14, local):** 1606 pytest tests and 154
  subtests pass (1 Windows-only test skipped), and the 12 standalone fixture suites
  complete. No live org or provider call.
- **Hook probe:** the spot-check's events and the git commands above were replayed
  through the real hook command (`python -I -c ...`) against a scratch build-only
  workspace under git. Each reported route exits 2; `EnterWorktree` into an
  existing worktree, `LSP` `hover` on a project file, `SendMessage`, `ExitWorktree`,
  `git clean -n` and `git status` exit 0.
- **CI:** `Validate Torque` on the pull request, all 9 cells (Ubuntu, macOS and
  Windows, each on Python 3.10, 3.12 and 3.14). The run id is recorded on the pull
  request.

## Review scope

The fixes follow the spot-check's recommendations and the review's suggested fix
for the git routes. They have not yet been re-reviewed independently. Until they
are, treat the list in [de-identified mode](ai-access.md) as the claim to check.

## Known remaining limits

De-identified mode is pattern matching on recognized tool calls, not a sandbox.
Beyond the limits in the alpha 11 record: `SendMessage` to a peer session that is
not gated; what a language server returns for an allowed `LSP` call, since its
index covers `clients/`; a `WorktreeCreate` hook that copies more than git and
`.worktreeinclude` would; and git aliases, stash objects read by hash, and a work
tree set in an earlier command. Bash from inside a worktree under
`.claude/worktrees/` is nearly always blocked, because its relative paths are
under `.claude/`. See [de-identified mode](ai-access.md) for the full list.
