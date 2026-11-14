---
id: L-20261114-5e03
title: LIKE does not work on the Field column of field permissions
scope: platform
kind: pitfall
state: active
when: a query on field permission rows fails with "invalid operator on id field"
do: filter Field with = or IN on the full Object.Field name, and add SobjectType = 'Object' to narrow by object
because: the Field column of FieldPermissions does not accept LIKE
applies:
  tasks: []
  types: []
  commands: []
  keywords: [FieldPermissions, SobjectType, LIKE]
  errors: ["invalid operator on id field"]
evidence:
  - {type: session, ref: "field-reported, one consultant's CLI transcripts over 2.5 weeks: 4 failed queries in 2 sessions", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: the rewritten query returns the rows of the field you named.
