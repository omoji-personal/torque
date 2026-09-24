# Validation for alpha 12: September 23, 2026

Alpha 12 is a development build with no package-index release. It changes
build-only mode (`src/torque/gate.py`), option parsing in the `torque` command, four
playbooks and documentation. Alpha 11 called the mode de-identified; it redacts
nothing, so it is now named after its `build-only` setting. The
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

A third review round then found more ordinary routes, all allowed:

- an MCP tool that runs a command (`start_process` with `cat clients/...`, or an
  `sf ... --target-org` command) skipped the Bash scan;
- `rg -uuu -eERROR ..`, `grep -R -eERROR ..` and `rg --files --hidden --no-ignore ..`
  from `project/`, where the attached pattern or the missing pattern made `..` read as
  the pattern;
- `tar -C.. -cf - .` from `project/`, where the `.` is read from the workspace root;
- `git clean -fdx .venv` over a hook environment inside the workspace (closed by the
  `git clean` check above);
- `torque doctor --clie example`, which the CLI expanded to `--client`;
- a `file://localhost/...` URI, whose host was kept as part of the path.

Each has a regression test in `tests/test_gate_alpha12.py`, committed before the
fix. 90 of that file's first 118 tests failed against the alpha 11 gate, and 40 of
the 82 round-three tests failed before their fixes (the git clean cases already
passed). Tests of the new name and the playbook sections were also written first. CI on
Linux and Windows then showed that a root at the filesystem root (`/`) did not count
as reaching `clients/` (macOS resolves `/tmp/..` to `/private`, which hid it); that has
a test too, and is fixed.

A scoped re-review of those fixes found two open routes, both now fixed with
tests written first (40 of 58 failed before the fix): old-style tar key bundles
(`tar cCf .. - .`) and `bsdtar`/`gtar`, and `git add -f .` staging ignored client
files that `git diff --cached` then printed, with the related read-back of a stash
that holds client files (`git log --all -p`, `git show 'stash^@'`). It also found
that re-entering an existing worktree by path was blocked when the worktree tracks
`workspace.json`; that works now.
One alpha 11
test that allowed `EnterWorktree` with a `name` in a workspace that is not a git
repository now expects a worktree path instead.

## Results

- **Offline suite (macOS, Python 3.12.14, local):** 1765 pytest tests and 154
  subtests pass (1 Windows-only test skipped), and the 12 standalone fixture suites
  complete. No live org or provider call.
- **Hook probe:** the spot-check's events and the git commands above were replayed
  through the real hook command (`python -I -c ...`) against a scratch build-only
  workspace under git. Each reported route exits 2; `EnterWorktree` into an
  existing worktree, `LSP` `hover` on a project file, `SendMessage`, `ExitWorktree`,
  `git clean -n` and `git status` exit 0. The round-three commands were replayed the same
  way, the `git clean` ones with the hook's interpreter in the workspace's `.venv`: each
  exits 2, and the same searches, `tar` and `git clean` confined to `project/` exit 0.
- **CI:** `Validate Torque` on the pull request, all 9 cells (Ubuntu, macOS and
  Windows, each on Python 3.10, 3.12 and 3.14). The run id is recorded on the pull
  request.

## Review scope

The fixes follow the spot-check's recommendations and the reviews' suggested
fixes. They have not yet been re-reviewed independently. Until they
are, treat the list in [build-only mode](ai-access.md) as the claim to check.

## Known remaining limits

Build-only mode is pattern matching on recognized tool calls, not a sandbox.
Beyond the limits in the alpha 11 record: `SendMessage` to a peer session that is
not gated; what a language server returns for an allowed `LSP` call, since its
index covers `clients/`; a `WorktreeCreate` hook that copies more than git and
`.worktreeinclude` would; an MCP tool that runs code from an argument other than
`command`, `cmd` or `script`; and git aliases, stash objects read by hash, and a work
tree set in an earlier command. Bash from inside a worktree under
`.claude/worktrees/` is nearly always blocked, because its relative paths are
under `.claude/`. See [build-only mode](ai-access.md) for the full list.
