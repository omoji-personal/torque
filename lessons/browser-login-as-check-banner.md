---
id: L-20261114-5e22
title: After Log in as, confirm whose session it is before every check
scope: platform
kind: pitfall
state: active
when: you are about to test as another user through Log in as
do: confirm the Logged in as banner and the user's name before the first check, and check the identity again after returning to the admin; one bounce to the login page means stop and sign in again
because: when the switch fails without a message the test runs as the admin, and an admin passes what the user cannot do
applies:
  tasks: [browser-test, access]
  types: []
  commands: ["torque browser login*--as-user*"]
  keywords: [Log in as, Login As, impersonate, banner]
  errors: []
evidence:
  - {type: session, ref: "field-reported, one consultant's working notes over eight weeks: 6 notes on Log in as under automation", date: 2026-11-14}
  - {type: session, ref: "field-reported, the predecessor toolkit's dated notes: the banner check, and a headless session that could not switch users", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: every result the test keeps names the user it ran as, read from the page header.
