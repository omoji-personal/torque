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

Each has a regression test in `tests/test_gate_alpha13.py`, committed before the
fix, next to an ordinary build-session counterpart that must still pass. 94 of that
file's first 208 gate tests failed against the alpha 12 gate; the blocks that already
held (`ln -s ../clients cl`, for example) and the ordinary cases passed. Three tests of
the documentation were also written first. Of the 23 tests for the second review's
findings, 14 failed before their fix; the 9 that passed are the `project/`-confined
equivalents that must stay allowed.

## Results

- **Offline suite (macOS, Python 3.13.15, local):** 2069 pytest tests and 154
  subtests pass (1 Windows-only test skipped), and the 12 standalone fixture suites
  complete. No live org or provider call.
- **Hook probe:** 81 hook events were piped through the real hook command
  (`python -I -c ...`) with `CLAUDE_PROJECT_DIR` set, against scratch workspaces made
  with `torque workspace init` and `ai-access build-only`: one without git holding
  `project/up -> ..`, and one with git at the root and `/clients/` ignored. Every route
  above exits 2. An ordinary session exits 0 from `project/` and from the root:
  `git status`, `diff`, `log`, `add`, `pytest`, `rg` and `grep -r` (also `rg -L` and
  `grep -R` over a project whose links stay inside it), `find -L`, `tar` and `zip` of
  `project/`, `cp -rL` and `rsync -aL` of `src/`, `ln -s` to a file in `project/`,
  a `for` loop over `"$f"`, `grep -rA2` and `zip -9r` of `src/`, `if cd src; then ...; fi`, `git status --ignored .` and
  `git ls-files -o` in `project/`, and `Glob`, `Grep` and `Read` in `project/`.
- **CI:** `Validate Torque` on the pull request, all 9 cells (Ubuntu, macOS and
  Windows, each on Python 3.10, 3.12 and 3.14). The run id is recorded on the pull
  request.

## Review scope

The fixes follow the review round's suggested fixes. At the time of writing they
have not been re-reviewed independently. Until they are, treat the list in
[build-only mode](ai-access.md) as the claim to check.

## Known remaining limits

Build-only mode is pattern matching on recognized tool calls, not a sandbox.
Beyond the limits in the alpha 12 record: a variable is read as a folder from the
current directory up to the workspace root, so one whose value spells part of a name
(`X=cli; cat ../${X}ents/...`) is not caught; a path from command output
(`cat "$(...)/clients/..."`) is still a command built at run time; the link walk stops
at 20,000 entries or 64 levels and blocks the command past either, so a following
tool over a very large tree is blocked; a link made by a route the gate does not
parse (a script, `cp -s`, a Windows junction made with `mklink`) is caught when a
path names it or a following tool walks into it, not when it is made; and the host's
own `Grep` and `Glob` tools are not walked for links, since whether they follow links
was not tested. See [build-only mode](ai-access.md) for the full list.
