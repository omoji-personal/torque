---
id: L-20261114-5e11
title: In an org with managed packages, test one to three record saves per anonymous script
scope: platform
kind: pitfall
state: active
when: "anonymous Apex or a test fails with \"Too many SOQL queries: 101\" while it saves only a few records in an org with managed packages"
do: "run one case, or at most three, per transaction: separate runs, or separate test methods with Test.startTest"
because: each save also runs the packages' own triggers, which spend queries of the same transaction, and a rollback gives none back
applies:
  tasks: [apex, testing]
  types: []
  commands: []
  keywords: [managed package, governor limit, test script]
  errors: ["Too many SOQL queries: 101"]
evidence:
  - {type: session, ref: "field-reported, one consultant's CLI transcripts over 2.5 weeks: 7 failed runs in 5 agent contexts of 3 sessions", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: the debug log's limit usage for one case shows SOQL queries well under 100.
