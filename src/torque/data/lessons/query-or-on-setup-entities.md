---
id: L-20261114-e9b2
title: OR is not supported in a WHERE on some setup entities
scope: platform
kind: pitfall
state: active
when: a query on a setup or metadata entity fails with "Disjunctions not supported"
do: replace the OR with IN on one field, or run one query per branch and combine the rows yourself
because: some setup and metadata entities accept only AND conditions in a WHERE clause
applies:
  tasks: [query]
  types: []
  commands: ["sf data query *"]
  keywords: [OR, WHERE, setup entity]
  errors: ["Disjunctions not supported"]
evidence:
  - {type: session, ref: "field-reported, one consultant over 2.5 weeks: 13 failed queries in 5 sessions", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: the rewritten query returns rows, and the branches together return what the OR would have.
