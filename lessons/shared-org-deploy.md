---
id: L-20261114-f15a
title: In an org other teams change, deploy only what you changed, from a fresh retrieve
scope: platform
kind: pitfall
state: active
when: you are about to deploy to an org other teams also change
do: build from that org's own current retrieve, retrieve each component again right before the deploy and compare, and deploy only the exact components you changed
because: a deploy from an older copy overwrites what others changed in the meantime, and it reports success
applies:
  tasks: [deploy, retrieve, shared-org]
  types: []
  commands: ["sf project deploy *", "torque deploy *"]
  keywords: [shared sandbox, overwrite, ignore-conflicts]
  errors: []
evidence:
  - {type: official-doc, ref: "https://github.com/salesforcecli/plugin-deploy-retrieve/blob/main/README.md", date: 2026-11-14}
  - {type: session, ref: "field-reported, one org", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: the retrieve after the deploy differs from the copy taken before it only by your additions.
