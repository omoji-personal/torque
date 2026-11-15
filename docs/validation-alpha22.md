# Validation for alpha 22

Alpha 22 is a practice release: a browser sign-in without a password, checks before and after a deploy, eight
Salesforce practice skills, a lessons loop that brings earlier lessons back at the moment of use, session records
that say what a check rested on, and one always-loaded block in the workspace `AGENTS.md`. It also carries the
October fix round (the previous draft pull request). This record separates what was run live, what the tests
cover, and what was not run.

## Observed results

CI on the tree of this release (`2eed1f9`, pull request 53): all nine jobs pass, each with 154 subtests.

| System | Python | Passed | Skipped |
|---|---|---|---|
| Linux | 3.10, 3.12, 3.14 | 10,293 | 50 |
| macOS | 3.10, 3.12, 3.14 | 10,257 | 86 |
| Windows | 3.10, 3.12, 3.14 | 9,561 | 782 |

Windows skips the tests that apply only on macOS and Linux and those that build a symbolic link; macOS skips the
real-Bash comparisons. Each job also ran the workflow sync checks and the provenance check, built the wheel and
source distribution, checked their contents and ran the installed smoke suite. The first CI run on this branch
found what the local runs on one machine could not: a Windows test whose setup cannot take effect on an elevated
runner, a temporary-file loop that hung for two hours on Python 3.10 (a product fix: a folder that refuses new
files now fails at once), a folder-access difference on older Python releases (see the known limits), and a test
of the sign-in relay that assumed one Python version's error page.

One gate timing test, `test_any_shape_below_the_limit_finishes_inside_the_budget[ansi-c]`, ran past its limit
(the 5-second budget plus 1 second) on macOS runners three times: twice on Python 3.10 (6.47 s on the pull
request's run of `7aa1511`, and once in the first round) and once on Python 3.12 in the run on `main` after the
merge (6.82 s). Each passed when rerun, and it has not failed on Linux or Windows; on Windows it takes 0.1 to
0.3 s. See the known limits for the likely cause.

On Windows 11 with Python 3.13, from the source tree, the full offline suite passed in 14 chunks (one pytest
process each, because the machine was short of memory): about 9,480 tests, all twelve standalone fixture suites,
and the first three chunks again after the last three fixes (1,369 passed). Each change was made with a test that
fails on the commit before it.

Run live on a Developer Edition org that holds made-up data only (a custom object with two fields and a
validation rule, a permission set, a before-save flow):

| What | Result |
|---|---|
| `torque browser login` in a host browser tab (Claude Code with its browser extension, Windows) | The tab was sent into the org; exit 0; no sign-in address or code in any output or in the tab list. The tab landed on the org's change-password page (that user's password had expired), so the sign-in step is proven and the pages after it are not |
| Validate, `torque check result`, `torque check metadata --changed-from` with Code Analyzer, deploy | As designed; the field without a grant was reported before the deploy; Code Analyzer's flow findings were merged with Torque's |
| `torque check deployed`, positive | Six components read back as submitted; the object's 36 properties that Salesforce adds on save recognised; the flow's latest version is its active one |
| `torque check deployed`, four negative cases | A changed formula, a revoked grant before and after its deploy, a permission set entry left out of a deploy, and a grant in the org that the file does not name: each reported as a difference with its reason |
| A permission set deploy | Replaces the set: an entry the file leaves out is removed in the org (project API version 62.0). This was not in any skill or lesson before; it is now in the build skill, the always-loaded block, a recipe, a shipped lesson, a check rule and the comparison |
| `torque lesson harvest --host claude` against a real host memory folder | 137 notes listed (counts only), nothing copied |
| `torque workspace upgrade` on a copy of a real customised workspace | Its own `AGENTS.md` kept byte for byte with the block appended; guard settings untouched |

A behaviour test gave real agents (Codex with `gpt-6.1-sol` at medium effort; Antigravity with its default
model) the same made-up tickets in workspaces built by the previous release and by this one, with a stub in place
of the Salesforce CLI. On a one-sentence request to add a field, the previous release shipped a field with no
access in every run (4 of 4 on Codex, 1 of 1 on Antigravity); this one shipped the grant in a permission set in
every run (11 runs across the two hosts) and ran `torque check metadata` on its change. With the always-loaded
block the agents also asked for lessons, marked them helpful, and in one Antigravity run saved a lesson
unprompted. Small numbers, two hosts.

## Remaining acceptance

Not verified in this release:

- Anything on a sandbox or a production org, where a flow deployed as active may not become the active version.
- Lightning pages after `torque browser login`, a second user (`--as-alias`, `--as-user`), and the scripted-browser
  mode (`--storage-state`).
- Claude Code as a host in the behaviour test, and every host on macOS.
- Under Antigravity: connected mode. (After the release, on 2026-11-14, a live Antigravity 1.5.9 session called
  the gate hook in build-only and full mode and loaded the build-only rule: see [hosts](hosts.md).)
- Connected mode with `torque check deployed`, live.

Known limits:

- The lessons scrub is a heuristic for known shapes; `docs/lessons.md` lists what gets through. A proposal to share
  a lesson more widely is read by a person.
- `torque check deployed` compares what was submitted with what the org holds; it does not show what a user sees,
  and a grant entry is not a user's effective access.
- On Windows, older Python releases ignore the owner-only mode Torque asks for when it creates a folder, so the
  folder inherits its parent's access (CI's Python 3.10.11 does; newer releases apply an owner-only list).
  Records and evidence files are still created private by Torque itself; the folders around them are private
  only where the Python release honours the mode. An explicit private-folder helper is planned.
- On macOS runners the gate can overrun its 5-second budget by about 2 seconds on one long ANSI-C quoting shape
  (above). The hook timeouts Torque writes (600 seconds for Claude Code, 30 for Antigravity) are not reached, so
  the call is decided, late. The likely cause, found after the release: the gate copied a folder path into the
  token for each of its unknown expansions and resolved the result (up to 174,160 characters) in one call that the
  budget does not interrupt; Windows refuses such a path at once, macOS walks it. The fix after alpha 22 keeps
  each variant to one folder path per expansion and refuses a word with more than 64 (see the changelog); it is
  not yet proven on a macOS runner.
- The known limits listed for alpha 21 remain, except where the changelog says otherwise.

No package-index release and no demo readiness is claimed.

## Review process

Two rounds of review by two outside model reviewers at their highest settings: an independent audit, then an
attack on the plan and the built code (one reviewer ran it). About 150 findings in the second round were triaged;
the ones that mattered were three ways lessons could carry one client's words to another, three ways the deployed
check could call a different component the same, errors of fact in the practice skills, and a skill that asked for
a fresh approval at every step. Each was fixed with a test that fails on the reviewed commit; reviewer claims that
did not hold were rejected with the reason. These reviews supplement the measured results and do not establish
the remaining live acceptance.
