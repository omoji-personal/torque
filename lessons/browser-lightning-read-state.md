---
id: L-20261114-5e21
title: In Lightning, go straight to the page and read state through the API or the shadow DOM
scope: platform
kind: works
state: active
when: you are about to read or click a Lightning page in a browser test
do: navigate straight to the record, object or Setup address, wait for it to render, and read values through the API or a script that walks shadow roots; Setup pages and flow screens opened from buttons sit in iframes
because: screenshots time out on heavy pages and text finders miss elements inside shadow DOM and iframes, so the test reports a failure a user would not see
applies:
  tasks: [browser-test]
  types: []
  commands: []
  keywords: [shadow DOM, iframe, screenshot, Lightning page]
  errors: []
evidence:
  - {type: session, ref: "field-reported, one consultant's CLI transcripts over 2.5 weeks: 39 screenshot timeouts, 35 elements not found, 11 null elements and 7 stale references in 3,324 browser calls", date: 2026-11-14}
  - {type: session, ref: "field-reported, one consultant's working notes over eight weeks: 6 notes", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: the value the test reads equals the value a query of the record returns.
