# Validation for alpha 21

Alpha 21 makes connected mode read a command the way the shell will run it, records consent
per org, and adds guarded reads: an off-by-default mode that answers build questions about an
org whose client agreed to metadata only. This record separates what was run live, what the
tests cover, and what was not run.

## Observed results

CI on the merged tree (`8e9d23e`, pull request 50): all nine jobs pass, each with 154 subtests.

| System | Python | Passed | Skipped |
|---|---|---|---|
| Linux | 3.10, 3.12, 3.14 | 7,994 | 29 |
| macOS | 3.10, 3.12, 3.14 | 7,958 | 65 |
| Windows | 3.10, 3.12, 3.14 | 7,251 | 772 |

Windows skips the tests that apply only on macOS and Linux (tier 2, file permission and owner
checks) and those that build a symbolic link. macOS also skips the real-Bash comparisons,
whose expectations are Bash 5's; macOS's `/bin/bash` is 3.2. Each job also ran the workflow
sync checks and the provenance check (180 carried-over files with their original source
hashes), built the wheel and source distribution, checked their contents and ran the
installed smoke suite.

On Windows 11 with Python 3.13, from the source tree, the full offline suite passed 6,320
tests on the tree before the last three commits (the provenance record and the macOS and
Linux fixes below).

Run live on a Developer Edition org that holds made-up data only, through a session started
with `torque launch` (Claude Code, auto permission mode). The client's consent gave that org
`counts`, `config_records` and `test_records`, and guarded reads were on.

| Round | Result |
|---|---|
| 1 | 20 of 22 steps as designed. `torque guarded fill` was refused by the host's own auto-mode check before the gate saw it. The Read tool opened a file outside the workspace, which the gate does not claim to cover. |
| 2 | Every command that should be refused was refused. Seven of eight guarded reads ran; `fill` was refused by the host check again, and the lane was renamed `fill-rate`. |
| 3 | Every guarded read ran, `fill-rate` included. A plain record query, turning guarded reads off, and a record create were refused, each with a next step. |

The gate's command readers are compared with the shells in the suite: real Bash in
`tests/test_gate_against_bash.py` and real PowerShell in `tests/test_gate_against_powershell.py`.
During review, every ordered pair of 154 PowerShell forms (46,065 lines) and of 113 Bash forms
(24,993 lines) was run against the shells with none missed, on the tree of review round 17.

## Remaining acceptance

Not verified in this release:

- Guarded reads and consent per org under Antigravity.
- The ordered-pair comparisons on the final tree. Later changes are covered by the suite and
  the live trials.
- The wording changes made after the third live round. A fourth round was not run.
- The tier 2 (delegated) paths, live. They run in CI on macOS and Linux only.
- The real-Bash comparisons on macOS's Bash 3.2.

Known limits:

- A host's own permission check can refuse a guarded read before the gate sees it, as Claude
  Code's auto mode did with `fill`.
- A path outside the workspace is not gated; a host's file tools can open it
  ([ai-access](ai-access.md)).
- Guarded reads are data minimization, not zero disclosure: counts, fill rates and the values
  the consultant released reach the session ([guarded reads](guarded-reads.md)).
- On Windows, Smart App Control can block the unsigned `torque.exe` launcher that pip writes at
  install. `python -m torque` runs the same code, and the gate hook already runs through Python.
- The known limits listed for alpha 20 remain, except where the changelog says otherwise.

No package-index release and no demo readiness is claimed.

## Review process

Twenty rounds of read-only review of the plan and the code, by an independent reviewer and an
auditor, mostly found lines the gate split differently from the shell; each was fixed and kept
as a test. The loop stopped after round 20 in favour of the live trials above. The first CI run
to reach the offline suite on this branch then found 18 failures in tests that run only on
macOS and Linux or on Python 3.10: one order defect (delegated sign-off looked for the consent
record before checking who was calling) and test assumptions about Windows, Bash 5 and newer
Python. All were fixed in `ecaa2a1`. One more run showed that the random-line Bash comparison
can put another line's call against a line on Bash 3.2; it skips there too since `8e9d23e`.
These reviews supplement the measured results and do not establish the remaining live
acceptance.
