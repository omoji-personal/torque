---
id: L-20261114-5e23
title: Reload fully, or start a new browser session, before you call a page fix failed
scope: platform
kind: pitfall
state: active
when: a browser test still shows the old behaviour of a page or layout you just deployed
do: hard-reload the page or start a new browser session, test again, and record the deploy time next to the result
because: Lightning caches rendered pages per session, so a test right after a deploy can show the version before it
applies:
  tasks: [browser-test, layout]
  types: [FlexiPage, Layout]
  commands: []
  keywords: [cache, hard reload, new session]
  errors: []
evidence:
  - {type: session, ref: "field-reported, one consultant's working notes over eight weeks: 6 notes on browser automation in Lightning", date: 2026-11-14}
  - {type: session, ref: "field-reported, the predecessor toolkit's dated notes: pages cache per session", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: after the reload, the page shows the element or text the deploy added.
