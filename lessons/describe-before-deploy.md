---
id: L-20261114-d3c1
title: Write the description before you deploy the component
scope: platform
kind: convention
state: active
when: you are about to deploy a field, flow, validation rule, permission set or class you built or changed
do: write its description in the source first, and help text on fields users fill in; if a script generates the flows, keep the descriptions as data and apply them again on every run
because: the description is the only place the next admin learns why the component exists, and generated metadata loses it without a word
applies:
  tasks: [deploy, documentation, field-build, flow-build]
  types: [CustomField, Flow, ValidationRule, PermissionSet, ApexClass, CustomObject]
  commands: ["sf project deploy *", "torque deploy *"]
  keywords: [description, help text, inlineHelpText]
  errors: []
evidence:
  - {type: session, ref: "field-reported, one consultant's work: 6 notes on descriptions added in a later deploy or lost when flows were regenerated; a scan of 257 built components in 7 client folders (help text on 14 of 78 new fields)", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: no changed component in the deploy set has an empty description. For the flows you touched, read `Description` on `FlowDefinitionView`.
