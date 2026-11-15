---
id: L-20261114-a16d
title: Edit Lightning page XML from a fresh retrieve, one exact property at a time
scope: platform
kind: pitfall
state: active
when: you are about to change Lightning page XML with a script or a bulk replace
do: start from a fresh retrieve and change only the exact property and value you mean to change
because: a broad replace in a Lightning page also removes labels and sections that look alike, and the page still deploys
applies:
  tasks: [layout, deploy]
  types: [FlexiPage]
  commands: []
  keywords: [Lightning page, section label, bulk replace]
  errors: []
evidence:
  - {type: session, ref: "field-reported, one org", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: diff the page XML you retrieved before the edit against the edited file, and after the deploy against a fresh retrieve; only the elements you meant to change differ.
