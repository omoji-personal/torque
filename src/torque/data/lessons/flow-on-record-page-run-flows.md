---
id: L-20261114-9c05
title: A screen flow on a record page needs Run Flows for its users
scope: platform
kind: pitfall
state: active
when: you are about to put a screen flow on a record page
do: give the intended users the Run Flows permission through a permission set, and send an explicit `false` to clear the flow's access override
because: without Run Flows the component does not render for those users, and an access override stays in force until it is set to false
applies:
  tasks: [flow-build, access, layout]
  types: [Flow, FlexiPage, PermissionSet]
  commands: []
  keywords: [Run Flows, access override, record page]
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
Check: signed in as one of those users, the component renders on the page.
