---
id: L-20261114-5e0b
title: Every flow version that reads a field blocks its deletion
scope: platform
kind: pitfall
state: active
when: you are about to delete a custom field by a destructive deploy, or the delete fails with "This custom field is referenced elsewhere"
do: remove the field from the flows first, delete the old flow versions that still read it (inactive ones too), then delete the field in a deploy of its own
because: a flow keeps all its versions, and a reference in any version, active or not, blocks the delete
applies:
  tasks: []
  types: []
  commands: ["*destructive*"]
  keywords: [destructiveChanges, flow version]
  errors: ["This custom field is referenced elsewhere"]
evidence:
  - {type: session, ref: "field-reported, one consultant's CLI transcripts over 2.5 weeks: 1 failed destructive deploy", date: 2026-11-14}
  - {type: session, ref: "field-reported, one consultant's working notes over eight weeks: 1 note on removing a tool by destructive deploy", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: a validate-only run of the destructive manifest passes before the real one. In the record, flows were removed by version (`Name-1`, `Name-2`).
