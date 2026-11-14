---
id: L-20261114-e9c3
title: Check that a field can be grouped before you GROUP BY it
scope: platform
kind: pitfall
state: active
when: a query fails because a field can not be grouped, or you are about to GROUP BY a field you have not checked
do: read the field's `groupable` flag in the describe; group by a field that has it or by a date function, or count the rows yourself
because: not every field can be grouped, and the query then fails as a whole
applies:
  tasks: [query]
  types: []
  commands: ["sf data query *"]
  keywords: [GROUP BY, groupable, aggregate]
  errors: ["can*not be grouped"]
evidence:
  - {type: session, ref: "field-reported, one consultant over 2.5 weeks: 13 failed queries in 4 sessions; the same query failed in four sub-agents of one session", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: `groupable` is true for each field in the GROUP BY, in the describe of this org.
