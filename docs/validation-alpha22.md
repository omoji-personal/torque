# Validation for alpha 22

Alpha 22 is a practice release: a browser sign-in without a password, checks before and after a deploy, eight
Salesforce practice skills, a lessons loop that brings earlier lessons back at the moment of use, session records
that say what a check rested on, and one always-loaded block in the workspace `AGENTS.md`. It also carries the
October fix round (the previous draft pull request). This record separates what was run live, what the tests
cover, and what was not run.

## Observed results

CI on the merged tree: CI_RESULT.

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
- Under Antigravity: the gate hook called by a live session, connected mode, and the mode rules loading.
- Connected mode with `torque check deployed`, live.

Known limits:

- The lessons scrub is a heuristic for known shapes; `docs/lessons.md` lists what gets through. A proposal to share
  a lesson more widely is read by a person.
- `torque check deployed` compares what was submitted with what the org holds; it does not show what a user sees,
  and a grant entry is not a user's effective access.
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
