---
id: L-20261114-5e1f
title: Replay an integration or guest process as the user it runs as
scope: platform
kind: pitfall
state: active
when: you are about to rely on automation or an integration that runs as an integration user, a site guest user or the automated process user
do: replay its steps as that user in a sandbox before handing it over; expect a default record type, a duplicate-rule exemption, connected app pre-authorization and access to the external credential to be missing
because: these users differ from an admin in exactly those places, so an admin test passes where the real run fails
applies:
  tasks: [integration, access]
  types: []
  commands: []
  keywords: [integration user, guest user, Automated Process, External Credential]
  errors: []
evidence:
  - {type: session, ref: "field-reported, a review of 166 rework episodes in 10 engagements, orgs per lesson not counted: 3 episodes holding 6 gaps", date: 2026-11-14}
  - {type: session, ref: "field-reported, one org (one consultant's working notes): 2 notes (an API-only profile failed on a missing record type; a guest user needed read on user external credentials)", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: one end-to-end run as that user creates the expected records without an error.
