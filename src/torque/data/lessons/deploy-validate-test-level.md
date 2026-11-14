---
id: L-20261114-5e06
title: Validate does not take NoTestRun; in a sandbox check with deploy start --dry-run
scope: platform
kind: pitfall
state: active
when: you are about to run `sf project deploy validate` with `--test-level NoTestRun`, or a deploy fails because NoTestRun is not allowed
do: in a sandbox, check without tests with `sf project deploy start --dry-run`; against production, leave the flag out or name the tests with RunSpecifiedTests
because: validate rejects NoTestRun, production rejects it on every deploy, and the CLI's own guide keeps validate for production and the dry run for sandboxes
applies:
  tasks: []
  types: []
  commands: ["sf project deploy validate*NoTestRun*"]
  keywords: [NoTestRun, dry-run, test level]
  errors: ["Expected --test-level=NoTestRun to be one of", "NoTestRun cannot be used in production"]
evidence:
  - {type: official-doc, ref: "https://github.com/salesforcecli/plugin-deploy-retrieve/blob/main/README.md", date: 2026-11-14}
  - {type: session, ref: "field-reported, one consultant's CLI transcripts over 2.5 weeks, orgs not counted: 4 failed deploy commands in 3 agent contexts", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: the dry run reports Succeeded; a validate against production returns a job id for `sf project deploy quick`.
