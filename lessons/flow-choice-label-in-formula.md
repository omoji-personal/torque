---
id: L-20261114-7a2e
title: A flow formula sees the label of a choice, not its value
scope: platform
kind: pitfall
state: active
when: you are about to compare a choice-based screen field in a flow formula
do: compare the field to the choice resource in a decision or a visibility rule instead of in a formula
because: a formula that reads a choice-based screen field gets the choice's label, so a comparison with the value never matches
applies:
  tasks: [flow-build]
  types: [Flow]
  commands: []
  keywords: [choice, screen flow, formula, picklist choice]
  errors: []
evidence:
  - {type: session, ref: "field-reported, one org", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: run the screen once per choice and confirm the branch that is not the default is taken.
