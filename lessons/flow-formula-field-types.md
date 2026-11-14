---
id: L-20261114-5e17
title: A flow formula cannot read a long text area, and a constant cannot be a DateTime
scope: platform
kind: pitfall
state: active
when: a flow deploy fails with "The formula expression is invalid" or "can't be DateTime"
do: copy a long text or multi-select value into a text variable before a formula reads it (or drop the reference), and use a formula resource where you wanted a DateTime constant
because: flow formulas do not accept long text area fields, accept multi-select picklists only in a few functions, and constants have no DateTime type
applies:
  tasks: []
  types: [Flow]
  commands: []
  keywords: [formula, long text area, multi-select picklist, constant]
  errors: ["The formula expression is invalid", "can*t be DateTime"]
evidence:
  - {type: session, ref: "field-reported, one consultant's CLI transcripts over 2.5 weeks, orgs not counted: 3 failed flow deploys", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: the describe shows no long text area among the fields a formula reads.
