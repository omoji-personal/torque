---
id: L-20261114-5e20
title: Sign the browser in from the CLI's authorization with torque browser login
scope: platform
kind: works
state: active
when: you are about to test in a browser in an org the Salesforce CLI is already authorized for
do: run `torque browser login --target-org ALIAS`, open the local address it prints in the controlled tab, wait until the path starts with /lightning/, and confirm the user before the first action; do not ask the consultant to sign in
because: the sign-in address is single-use and expires 60 seconds after it is made, so it must be made at that moment and never shown, and a test that starts at a login page wastes the run
applies:
  tasks: [browser-test, sign-in]
  types: []
  commands: []
  keywords: [front door, frontdoor, login page, Playwright]
  errors: []
evidence:
  - {type: official-doc, ref: "https://github.com/forcedotcom/cli/blob/main/releasenotes/README.md", date: 2026-11-14}
  - {type: session, ref: "field-reported, one consultant's working notes over eight weeks: 9 notes in which the consultant asked for front-door sign-in", date: 2026-11-14}
  - {type: session, ref: "field-reported, one consultant's CLI transcripts over 2.5 weeks: 10 of 36 browser sequences in an org began at a login page", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: the tab shows a /lightning/ page as the intended user, and no sign-in address appears in the transcript.
