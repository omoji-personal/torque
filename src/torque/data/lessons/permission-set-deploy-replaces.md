---
id: L-20261114-a7c2
title: A permission set deploy replaces the set; what the file leaves out is removed
scope: platform
kind: pitfall
state: active
when: you are about to deploy a permission set that already exists in the org, for example to add the grant for a new field
do: retrieve the permission set first and deploy its full file with your entry added, or put the new grants in a new permission set; never deploy a copy that holds only the new entries
because: the deploy replaces the set's entries, so every grant the file does not list is removed in the org, and the deploy still reports success
applies:
  tasks: [deploy, access, field-build]
  types: [PermissionSet]
  commands: ["sf project deploy *PermissionSet*", "torque deploy *PermissionSet*"]
  keywords: [permission set, fieldPermissions, replace, overwrite]
  errors: []
evidence:
  - {type: reproduced, ref: "reproduced on a practice org (Developer Edition, project API version 62.0): a permission set deployed without one of its field entries lost that grant; the next read of FieldPermissions showed no row", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified: 2026-11-14
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: before the deploy, compare the file with the retrieved permission set (`torque check metadata PATH --baseline RETRIEVED_DIR`); after it, `torque check deployed` reads the set back and the FieldPermissions query shows the grants you expect.
Not covered: profiles behave differently (a profile deploy changes only what the file names); Salesforce's PermissionSet reference states the replace behaviour for API version 40.0 and later, so an older project API version may merge instead.
