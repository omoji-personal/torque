---
id: L-20261114-9c05
title: A screen flow on a record page needs run access for its users, given the way the flow intends
scope: platform
kind: pitfall
state: active
when: you are about to put a screen flow on a record page
do: "first read whether the flow restricts access to enabled profiles or permission sets (`isAdditionalPermissionRequiredToRun`): if it does and that is intended, keep the restriction and grant the flow to the intended users with `flowAccesses` in a permission set; if it does not, give them Run Flows through a permission set; only if the restriction is set but not intended, write the element as an explicit `false`"
because: without run access the record page hides the component from those users, and (field-reported) leaving the element out of a new version's metadata keeps an earlier `true`
applies:
  tasks: [flow-build, access, layout]
  types: [Flow, FlexiPage, PermissionSet]
  commands: []
  keywords: [Run Flows, access override, record page, isAdditionalPermissionRequiredToRun, flowAccesses]
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
Check: signed in as one of the intended users, the component renders on the page; when the restriction is intended, signed in as a user without the grant, it does not.
