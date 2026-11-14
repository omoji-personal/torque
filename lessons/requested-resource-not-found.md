---
id: L-20261114-5e05
title: On "The requested resource does not exist", check the API version and the user's access first
scope: platform
kind: pitfall
state: active
when: a CLI or REST call fails with "The requested resource does not exist", above all just after a sandbox refresh
do: after a refresh pass the API version explicitly (`--api-version`); for a REST request by Id, query the record first to confirm the running user can see it and that the path fits the API version
because: the CLI caches an org's API version for up to a day and a refreshed sandbox may not serve it, and a REST read of a record the user cannot see gives the same answer
applies:
  tasks: [sandbox-refresh]
  types: []
  commands: []
  keywords: [api-version, NOT_FOUND, REST]
  errors: ["The requested resource does not exist"]
evidence:
  - {type: session, ref: "field-reported, one consultant's CLI transcripts over 2.5 weeks: 16 failed REST requests in 5 sessions, half never resolved", date: 2026-11-14}
  - {type: session, ref: "field-reported, one consultant's working notes over eight weeks: 2 notes on every CLI call failing this way after a refresh", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: an Organization query with `--api-version` succeeds, and the record query returns the row as the same user.
