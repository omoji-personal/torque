---
id: L-20261114-5e1a
title: Choose a rule's condition from the real data, not from a few examples
scope: platform
kind: pitfall
state: active
when: you are about to choose the condition a validation rule, flow entry condition, filter or report uses to sort existing records
do: count the real values first, read-only in production, with an aggregate query per candidate field (GROUP BY the field), and say which values the rule takes in and which it leaves out
because: real data holds variants and placeholder values that a few examples do not show, and a rule built from examples misses them without any error
applies:
  tasks: [validation, flow-build, data-quality]
  types: [ValidationRule, Flow]
  commands: []
  keywords: [GROUP BY, entry condition, filter, real data]
  errors: []
evidence:
  - {type: session, ref: "field-reported, a review of 166 rework episodes in 10 engagements, orgs per lesson not counted: 14 episodes", date: 2026-11-14}
  - {type: session, ref: "field-reported, one org (one consultant's working notes): 4 notes (a first rule missed about a hundred real cases)", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: a table of the values with their counts, each marked in or out, is saved with the change.
