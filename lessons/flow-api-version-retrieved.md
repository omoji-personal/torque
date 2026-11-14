---
id: L-20261114-5e18
title: Deploy a retrieved flow at the API version it was retrieved at, never lower
scope: platform
kind: pitfall
state: active
when: a deploy fails with "not valid in version", or you are about to change a flow's apiVersion or the project's sourceApiVersion
do: set sourceApiVersion in sfdx-project.json to at least the version the flow was retrieved at, and raise a flow's own apiVersion only as a tested change
because: a flow retrieved at a newer version carries properties an older version does not know, and the API version changes how the flow runs
applies:
  tasks: []
  types: [Flow]
  commands: []
  keywords: [apiVersion, sourceApiVersion]
  errors: ["not valid in version"]
evidence:
  - {type: session, ref: "field-reported, one consultant's CLI transcripts over 2.5 weeks: 2 failed deploys", date: 2026-11-14}
  - {type: session, ref: "field-reported, a scan of 34 flows built in real work: five API versions from 62.0 to 68.0", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: `torque check metadata` reports no `flow.api-version-behind` error.
