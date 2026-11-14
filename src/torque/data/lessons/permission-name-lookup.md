---
id: L-20261114-5e09
title: Take user permission names from the org, not from memory
scope: platform
kind: pitfall
state: active
when: a permission set deploy fails with "Unknown user permission", or you are about to write userPermissions into a permission set by hand
do: look the name up among the `Permissions...` fields of PermissionSet in the target org's describe, and use it without the `Permissions` prefix
because: a user permission's API name differs from its label and some exist only with a feature or licence, and one unknown name fails the deploy
applies:
  tasks: [access]
  types: [PermissionSet]
  commands: []
  keywords: [userPermissions]
  errors: ["Unknown user permission"]
evidence:
  - {type: session, ref: "field-reported, one consultant's CLI transcripts over 2.5 weeks, orgs not counted: 1 failed deploy and 3 failed queries", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: every userPermissions name in the file appears as Permissions<Name> in `sf sobject describe --sobject PermissionSet --target-org ALIAS`.
