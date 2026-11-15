---
id: L-20261114-5e1b
title: Count what a first recalculation or backfill will write before you run it
scope: platform
kind: pitfall
state: active
when: you are about to run a first full recalculation, a rollup rebuild or a backfill over existing records
do: count the records the first run will change and list what fires on those writes; limit the first run to the records that need it, then widen it
because: a first run can write every record, and each write runs the object's update automation, integrations included
applies:
  tasks: [rollup, data-load]
  types: []
  commands: []
  keywords: [recalculation, backfill, full calculate, mass update]
  errors: []
evidence:
  - {type: session, ref: "field-reported, a review of 166 rework episodes in 10 engagements, orgs per lesson not counted: 4 episodes (a first recalculation wrote to every sandbox record, noticed three days later)", date: 2026-11-14}
  - {type: session, ref: "field-reported, one org (one consultant's working notes): 1 note on a staged production rollup rollout", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: the number of records changed by the first run matches the number you predicted.
