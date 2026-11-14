---
id: L-20261114-5e15
title: sf org logout does not revoke the token; revoke it in the org first
scope: platform
kind: pitfall
state: active
when: you are about to end a CLI authorization to a production org, or to any org whose access must stop
do: revoke the CLI's OAuth grant in the org (the user's OAuth connected apps in Setup) and then run `sf org logout`
because: "`sf org logout` deletes only the local authorization, and the refresh token keeps working until it is revoked"
applies:
  tasks: [sign-in, production]
  types: []
  commands: ["sf org logout*"]
  keywords: [revoke, refresh token, OAuth]
  errors: []
evidence:
  - {type: session, ref: "field-reported, one org (one consultant's working notes): 5 notes (a deactivated user's tokens kept an integration signing in nightly until revoked)", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: the user's OAuth connected apps list in Setup no longer shows the CLI.
