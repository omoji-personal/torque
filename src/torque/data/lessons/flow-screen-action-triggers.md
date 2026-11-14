---
id: L-20261114-8b4f
title: A screen action deployed from metadata needs its triggers
scope: platform
kind: pitfall
state: active
when: you are about to deploy a screen flow whose screen runs an action, written as metadata
do: include the action's `<triggers>` element, with real event names, in the screen's metadata
because: a screen action saved without its triggers deploys cleanly and never runs
applies:
  tasks: [flow-build, deploy]
  types: [Flow]
  commands: ["sf project deploy *", "torque deploy *"]
  keywords: [screen action, triggers, screen flow]
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
Check: the action runs when the screen loads in a browser test. A screen with `<actions>` and no `<triggers>` is a finding.
