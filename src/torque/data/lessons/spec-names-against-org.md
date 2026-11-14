---
id: L-20261114-5e1e
title: Resolve every name in a spec against the org before you design
scope: platform
kind: pitfall
state: active
when: you are about to build or design from a spec, a story or a ticket that names objects, fields, stages or picklist values
do: look up each object, field API name and picklist or stage value in the target org's describe, and send the mismatches back as questions
because: specs name fields one word off, stages that do not exist and values that differ between sandboxes
applies:
  tasks: [field-build, design, requirements]
  types: []
  commands: []
  keywords: [spec, story, API name, picklist value, stage]
  errors: []
evidence:
  - {type: session, ref: "field-reported, one consultant's working notes over eight weeks: 6 notes", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: a describe result for each name in the spec is saved with the design.
