---
id: L-20261114-5e0f
title: Read anonymous Apex results from --json, not through a narrow filter
scope: platform
kind: works
state: active
when: you are about to run `sf apex run` and filter its output, or a run failed and the visible output does not say why
do: run it with `--json` and read `compileProblem`, `exceptionMessage`, `line` and `column` from the result
because: the reason follows the first error line, so a filter that keeps that line hides it and the run has to be repeated
applies:
  tasks: [apex]
  types: []
  commands: ["sf apex run*"]
  keywords: [compileProblem, exceptionMessage, anonymous Apex]
  errors: []
evidence:
  - {type: session, ref: "field-reported, one consultant's CLI transcripts over 2.5 weeks: 8 failures with the reason hidden and 12 unexplained exits in 311 runs", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: the saved result shows `success`, and on a failure the problem with its line and column.
