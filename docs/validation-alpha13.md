# Validation for alpha 13: September 24, 2026

Alpha 13 is a development build with no package-index release. It changes
build-only mode (`src/torque/gate.py`) and documentation. The
[alpha 12 record](validation-alpha12.md) and the [alpha 11 record](validation-alpha11.md)
still describe the earlier fixes; alpha 12's "Review scope" now records the
re-review and spot-checks that followed it.

## What prompted it

A later review round drove the alpha 12 gate with hook events through the real hook
command, against scratch build-only workspaces, and ran each allowed route for real.
From `project/`, these were allowed and read a client file:

- a symbolic link out of the tree: `ln -s .. up` (or to any folder above the
  workspace), then a recursive tool that follows links, such as `rg -L`, `find -L`,
  `grep -R`, `tar -h`, `cp -rL`, `rsync -L`, `zip -r`, `fd -L`, `ls -RL` or `tree -l`.
  A link already in a supplied project works the same way. The gate resolved a link
  a path names, but not one a tool finds during its walk;
- a variable set in the same command: `R=..; rg SECRET $R`, `D=..; cat "$D/clients/..."`
  and `cat "${PWD%/project}/clients/..."`;
- a `cd` inside `if`: `if cd ..; then rg SECRET; fi`, and
  `if true; then cd ..; fi; cat clients/...`;
- `grep -d recurse` and `grep --directories=recurse`, which search a tree like `-r`.

It also found that `git status --ignored` and `git ls-files -o -i` list the names of
files under `clients/`, and that a `Glob` pattern starting with `../` was not read as
climbing out of the current directory.

A second review of the same release found that a short-option cluster holding a
digit was not read as recursive: `grep -rA2 ERROR ..`, `grep -rnA2 ERROR ..`,
`zip -9r - ..` and `zip -r9 - ..` were allowed from `project/`. It also found that
`torque doctor` and an earlier paragraph of the alpha 12 record still said `git log`
passes while client files are tracked; the final rule allows only `git status`
without `-v`/`--verbose` and `git rm --cached` of paths under `clients/`.

A third review of the same release found that `git apply` of a patch that sets
`ai_access` to `full` was allowed and turned the mode off, and that `git am`,
`patch -p1 <` and archive extraction could likewise write over `workspace.json`, the
hook configuration or `clients/`. The limits text named only files that replace the
gate, and contradicted the list of what is blocked. It also found the same stale
`status and log` wording in the gate's own description, and a changelog that called
the tagged alpha 12 "unpublished".

Each has a regression test in `tests/test_gate_alpha13.py`, committed before the
fix, next to an ordinary build-session counterpart that must still pass. 94 of that
file's first 208 gate tests failed against the alpha 12 gate; the blocks that already
held (`ln -s ../clients cl`, for example) and the ordinary cases passed. Three tests of
the documentation were also written first. Of the 23 tests for the second review's
findings, 14 failed before their fix; the 9 that passed are the `project/`-confined
equivalents that must stay allowed. Of the 60 tests for the third review's findings,
39 failed before their fix; the 21 that passed are the confined cases (`git apply`
and `patch` from `project/`, extraction into `project/`, `--check`, `--stat`,
`--dry-run`, listings). After the re-review below, the tests of the per-call link walk
were replaced by tests of the rulings: no tree walk per call, the link-making rule,
the doctor scan, over-long words and the hook timeout. 31 of them failed before the
change.

## Results

- **Offline suite (macOS, Python 3.13.15, local):** 2113 pytest tests and 154
  subtests pass (1 Windows-only test skipped), and the 12 standalone fixture suites
  complete. No live org or provider call.
- **Hook probe:** 98 hook events were piped through the real hook command (`python -I -c
  ...`) with `CLAUDE_PROJECT_DIR` set, against scratch workspaces made with `torque
  workspace init` and `ai-access build-only`: one without git holding `project/up ->
  ..`, and one with git at the root and `/clients/` ignored. Every route above exits 2,
  except a recursive tool following the existing link (`rg -L`, `grep -R`, `find -L`,
  `zip -r` and the rest), which exits 0 as documented; `torque doctor` on that workspace
  reports `project/up` and is not ready. Naming the link (`rg SECRET up`), making a link
  with `ln -s ..`, `mklink /D` or `New-Item`, and the patch that sets `ai_access` to
  `full` (through `git apply` at the root, `git am` from `project/` and `patch -p1 <`)
  exit 2. An ordinary session exits 0 from `project/` and from the root: `git status`,
  `diff`, `log`, `add`, a 400-character `git commit -m`, a 30-command `echo` chain,
  `pytest`, `rg` and `grep -r`, `tar` and `zip` of `project/`, `ln -s` to a file in
  `project/`, a `for` loop over `"$f"`, `grep -rA2` and `zip -9r` of `src/`, `if cd src;
  then ...; fi`, `git status --ignored .` and `git ls-files -o` in `project/`, `git
  apply` of a confined patch, `--check`, `patch --dry-run`, extraction into `src/`,
  `unzip -l`, and `Glob`, `Grep` and `Read` in `project/`.
- **Budget probe:** in a scratch workspace holding 15,000 links onto a 1,000-link chain
  (made with gate-allowed `ln -s` loops), 48 globs over those links followed by a read of
  a client file exit 2 through the real hook in 5.05 seconds, with the budget message.
- **CI:** `Validate Torque` on the pull request, all 9 cells (Ubuntu, macOS and
  Windows, each on Python 3.10, 3.12 and 3.14). The run id is recorded on the pull
  request. An earlier head's first run failed one test on Windows, in the per-call link walk
  that the re-review below led to removing.

## Review scope

The first fixes (through a356093) followed the review round's suggested fixes: the
gate walked each search root of a link-following tool, up to 20,000 entries and 64
levels, and blocked when a link led to `clients/` or above it. A security re-review
of a356093 returned "fix first". It found N1, N4, N5 and the option-cluster and doctor
fixes addressed. N3 was not: the named cases blocked, but close variants still read
`clients/` for real (macOS `cp -r`, which follows links; BSD grep `-S`; zsh's `***/`;
ripgrep's configuration file; a link made, moved or unlocked earlier in the same
command). The walk could also be slowed past the hook's timeout, and a timed-out hook
lets the call run. It over-blocked ordinary work in any project with a real
`node_modules`. Separately, a word longer than a file name, such as a long commit
message, failed every call closed; that predates alpha 13.

The owner's rulings: remove the per-call walk; block making a link that leads out of
the tree (`ln`, `cp -s`, `mklink`, `New-Item`) and keep resolving a path that names a
link; add a one-time `torque doctor` scan that reports such links as not ready; treat
an over-long word as not a path; keep the gate free of filesystem walks and document
the hook timeout; and list the remaining link variants as limits, since the real
control is an agent account that holds no client files. Those changes were made with
tests written first. Patch and archive checks from a third review of the same release
(da4e9a5 to f2c1c18) landed while the re-review ran, so it did not cover them.

A spot-check of the redesign (9a56c8c) and of those patch and archive checks then
returned "fix first" again. The link rule, the resolution of named links, the doctor
scan, over-long words, the patch and archive checks and all 150 ordinary cases held,
and the earlier over-blocks in large projects were gone. Two gaps remained. Glob
expansion still resolved every match with no time limit: a gate-allowed chain of links
made `ls` over one glob take 13.6 seconds, and 48 of them followed by a read of a
client file took 646 seconds, past the hook timeout, so the read would have run. And
`unzip -od..` or `-qod ..` extracted into the workspace root, where a crafted archive
set `ai_access` to `full`. It also noted that doctor missed a link reaching `clients/`
through an outside folder, and did not check the hook's timeout.

The rulings, each fixed with tests written first: one time budget per call (5
seconds) checked during path resolution, glob expansion and git queries, and a budget
of 10,000 glob matches, with a block past either (the same 48-glob command now blocks
in about 5 seconds); unzip option clusters read like getopt; doctor resolves each link
fully and follows links into outside folders; and doctor warns when the hook entry has
no `timeout` or a larger one. A command longer than the longest path the system
accepts is also no longer read as a path that fails the call closed. These fixes have
not been spot-checked yet.

## Known remaining limits

Build-only mode is pattern matching on recognized tool calls, not a sandbox.
Beyond the limits in the alpha 12 record: a link that already leads to `clients/` or
above it, or one made in the same command by a route the link rule does not see (`mv`
or `cp -P` of a link, a script, an archive, `git checkout`), can be followed by a
recursive tool (`rg -L`, `grep -R`, macOS `cp -r`, zsh `***/`, ripgrep configured to
follow). The gate does not walk the tree on each call; `torque doctor` finds such links
once, outside `clients/`, `node_modules` and `.git`, up to 200,000 entries. A variable
is read as a folder from the current directory up to the workspace root, so one whose
value spells part of a name (`X=cli; cat ../${X}ents/...`) is not caught; a path from
command output (`cat "$(...)/clients/..."`) is still a command built at run time. A patch
`git apply` reads from standard input inside `project/` is left to git's own path checks,
an archive member that is a link a later member writes through is left to the
extractor, and extractors other than `tar`, `bsdtar`, `unzip` and `ditto -x` are not
parsed. A hook that times out lets the call proceed; the gate's own 5-second budget
blocks first, but a single path resolution or glob step already running when the
budget ends finishes before the check. See [build-only mode](ai-access.md) for the full list.
