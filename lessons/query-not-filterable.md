---
id: L-20261114-5e01
title: A long text field cannot be filtered in a WHERE clause
scope: platform
kind: pitfall
state: active
when: a query fails because a field can not be filtered, or you are about to filter on a long text area or a setup field you have not checked
do: read the field's `filterable` flag in the describe; filter on a field that has it and narrow the rest in your own code
because: long text areas and several setup fields are not filterable, and the whole query fails
applies:
  tasks: []
  types: []
  commands: []
  keywords: [WHERE, filterable, long text area]
  errors: ["can*not be filtered"]
evidence:
  - {type: session, ref: "field-reported, one consultant's CLI transcripts over 2.5 weeks, orgs not counted: 7 failed queries in 4 sessions", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: `filterable` is true in this org's describe for every field in the WHERE clause.
