---
id: L-20261114-e9d4
title: On "sObject type is not supported", try the other API before anything else
scope: platform
kind: pitfall
state: active
when: a query fails with "sObject type ... is not supported", or you are about to add or drop `--use-tooling-api`
do: run the same query on the other API (with or without `--use-tooling-api`) and confirm the object exists with `sf sobject list`; decide the API per entity and note which one served it
because: some setup entities are served only by the Tooling API and others only by the standard API, and the records of a custom object never by the Tooling API
applies:
  tasks: [query]
  types: []
  commands: ["sf data query *"]
  keywords: [Tooling API, use-tooling-api]
  errors: ["sObject type * is not supported"]
evidence:
  - {type: session, ref: "field-reported, one consultant over 2.5 weeks: 19 failed queries in 6 sessions (the wrong API for the entity, or an object that is not in the org)", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: the query returns rows on one of the two APIs; if on neither, the object is not in this org.
