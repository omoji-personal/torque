# Validation for alpha 14: September 24, 2026

Alpha 14 is a development build with no package-index release. It changes
build-only mode (`src/torque/gate.py`), the README's opening section and
documentation. The [alpha 13 record](validation-alpha13.md) still describes the
earlier fixes, and now records the gap below and that alpha 14 closes it.

## What prompted it

A spot-check of the released alpha 13 (773f786) drove the gate through the real hook
command against a scratch build-only workspace. The watchdog added in alpha 13 is a
thread, and one regular-expression call does not let it run until the call returns.
The brace-expansion pattern scanned to the end of a run of word characters from every
starting position, so its time grew with the square of the command's length, and
nothing limited that length:

- the pattern alone took 5.2 seconds on 30,000 characters and 20.7 seconds on 60,000;
- through the hook, `echo` of 60,000 characters followed by a read of a client file
  exited 2 after 21 seconds, and with 120,000 characters after 83.5 seconds.

About 105,000 characters would outrun a 60-second host timeout, and a hook that times
out lets the call run. A run of commas after an open brace (`{a,a,a,...`) had the same
shape inside the group.

## The change

- `MAX_INPUT_CHARS` (20,000): `decide()` blocks a call holding a longer command, path or
  other argument the gate parses before any pattern runs, and says so. The contents a
  file tool writes (`Write`, `Edit`, `MultiEdit`, `NotebookEdit`) are not parsed and not
  limited. The Bash scan repeats the check for any other caller.
- Brace expansion matches only the `{a,b}` group, whose first part stops at the first
  comma, and finds the prefix and suffix with a scan. It takes linear time and gives the
  same result as the alpha 13 pattern: the tests compare the two on 4,000 random short
  inputs and 18 fixed cases.
- An expansion that would grow a command past `MAX_EXPANDED_CHARS` (80,000) is blocked.

The tests are in `tests/test_gate_alpha14.py`, committed before the fix (edfadf4, then
cdd5c21). Of that file's 52 gate tests, 23 failed against the alpha 13
gate, including every timing test on a long input; the ones that passed are the
brace-expansion comparisons, the ordinary cases (a 5,000-character commit message, a
workspace not in build-only mode) and input shapes the old pattern already handled
quickly. (The two hook tests load the checkout's own source, so that run measured the
fix for them.) One alpha 13 test of the build-only document, which forbade the old walk's
"20,000", now forbids "20,000 entries".

## Results

- **Offline suite (macOS, Python 3.14.7, local):** 2175 pytest tests and 154
  subtests pass (1 Windows-only test skipped), and the 12 standalone fixture suites
  complete. No live org or provider call.
- **Hook probe:** events piped through the real hook command (`python -I -c ...`) with
  `CLAUDE_PROJECT_DIR` set, from `project/` in a scratch build-only workspace:
  `echo` of 120,000 characters followed by a read of a client file exits 2 in 0.05
  seconds (83.5 seconds in alpha 13); 320,000 characters and a 60,000-character run of
  commas after a brace exit 2 in 0.04 seconds, each with the length message. Just under
  the limit, 19,960 characters followed by the same read exits 2 in 0.06 seconds, blocked
  as a read of client context. A `git commit -m` with a 5,000-character message exits 0
  in 0.18 seconds, `git status` exits 0 and `cat ../clients/acme/notes.md` exits 2.
- **CI:** `Validate Torque` on the pull request, all 9 cells (Ubuntu, macOS and
  Windows, each on Python 3.10, 3.12 and 3.14). The run id is recorded on the pull
  request.

## Review scope

The fix follows the spot-check's first suggested remedy (fail closed on any command or
string over a fixed length before any regular expression runs) and its second (make
the brace pattern linear). The other whole-command patterns were timed on 20,000-character
adversarial inputs (`${` repeated, `stash@{` repeated, `$'` repeated, open parentheses);
each finishes well inside the 5-second budget, and path work past the budget still
blocks. This build has not yet been re-reviewed.

## Known remaining limits

Unchanged from the [alpha 13 record](validation-alpha13.md), except that a long command
no longer holds the gate past its watchdog. A command longer than 20,000 characters is
blocked in build-only mode even when it is harmless; put long text in a file and pass
the file (`git commit -F msg.txt`). See [build-only mode](ai-access.md) for the full list.
