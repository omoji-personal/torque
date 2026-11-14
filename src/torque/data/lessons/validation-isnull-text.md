---
id: L-20261114-5e1d
title: ISNULL never fires on a text field; use ISBLANK
scope: platform
kind: pitfall
state: active
when: you are about to write a validation rule or formula that tests whether a text field is empty
do: use ISBLANK(field) for text fields, not ISNULL(field)
because: a text field is never null in a formula, so ISNULL on it is always false and the rule never fires
applies:
  tasks: [validation]
  types: [ValidationRule]
  commands: []
  keywords: [ISNULL, ISBLANK, formula]
  errors: []
evidence:
  - {type: session, ref: "field-reported, one consultant's working notes over eight weeks: 1 note among build facts found by trial", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: in a sandbox, save a record with the field empty; the rule fires.
