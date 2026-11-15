---
id: L-20261114-5e02
title: Some setup entities refuse COUNT(Id) with GROUP BY
scope: platform
kind: pitfall
state: active
when: a grouped count fails with "does not support aggregate operator COUNT"
do: use COUNT() without GROUP BY (one query per group value), or fetch the rows and count them yourself
because: on some setup and metadata entities the Id field cannot be aggregated, and the query fails as a whole
applies:
  tasks: []
  types: []
  commands: []
  keywords: [COUNT(Id), GROUP BY, aggregate]
  errors: ["does not support aggregate operator"]
evidence:
  - {type: session, ref: "field-reported, one consultant's CLI transcripts over 2.5 weeks, orgs not counted: 6 failed queries in 6 agent contexts of 2 sessions", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: the counts per group add up to the total from SELECT COUNT() on the same entity.
