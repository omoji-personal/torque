---
id: L-20261114-e9a1
title: Look a field name up before you query it, and never guess twice
scope: platform
kind: pitfall
state: active
when: a query fails on an unknown field or relationship name, or you are about to query a field you have not seen in a describe or in metadata this session
do: look the name up with `sf sobject describe` (or a FieldDefinition query) and use the name it gives; after an unknown-name error do not try another guess
because: a second guess usually fails as well, while a describe answers in one call; setup entities and package objects rarely use the name you expect
applies:
  tasks: [query]
  types: []
  commands: ["sf data query *"]
  keywords: [describe, FieldDefinition, field name]
  errors: ["No such column", "Didn't understand relationship"]
evidence:
  - {type: session, ref: "field-reported, one consultant over 2.5 weeks: 52 failed queries on an unknown name, and another guess followed in 37 of 51", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: every field path in the query appears in the describe of its object, in this org.
