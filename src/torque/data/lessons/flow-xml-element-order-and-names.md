---
id: L-20261114-5e16
title: In hand-edited Flow XML, keep same-tag elements together and every name unique
scope: platform
kind: pitfall
state: active
when: a flow deploy fails with "is duplicated at this location in type Flow" or "Duplicate developer name", or you are about to add elements to Flow XML by hand
do: put a new element next to its siblings of the same tag, and give every element, outcome and resource an API name that is unique ignoring case
because: the Metadata API rejects same-tag elements that are split apart, and it compares flow names without case
applies:
  tasks: [flow-build]
  types: [Flow]
  commands: []
  keywords: [Flow XML, API name, hand edit]
  errors: ["is duplicated at this location in type Flow", "Duplicate developer name"]
evidence:
  - {type: session, ref: "field-reported, one consultant's CLI transcripts over 2.5 weeks: 2 failed flow deploys", date: 2026-11-14}
  - {type: session, ref: "field-reported, one consultant's working notes over eight weeks: 6 notes on flow deploy traps", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: `torque check metadata` reports no `name.case-collision` finding, and a dry run of the flow passes.
