---
id: L-20261114-5e04
title: Some setup entities answer only with the filter they require
scope: platform
kind: pitfall
state: active
when: a query fails with "Implementation restriction"
do: read the rest of the message, which names the filter the entity requires (an Id with =, or a parent key), add exactly that filter, and run one query per key when you need several
because: several setup and metadata entities refuse a query that lacks their mandatory filter
applies:
  tasks: []
  types: []
  commands: []
  keywords: [setup entity, mandatory filter]
  errors: ["Implementation restriction"]
evidence:
  - {type: session, ref: "field-reported, one consultant's CLI transcripts over 2.5 weeks, orgs not counted: 3 failed queries in 3 sessions", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: the query with the required filter returns rows for a key you know exists.
