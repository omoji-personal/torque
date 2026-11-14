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
  - {type: official-doc, ref: "https://help.salesforce.com/s/articleView?id=sf.flow_prep_bestpractices.htm&type=5", date: 2026-11-14}
  - {type: session, ref: "field-reported, one org", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified: 2026-11-14
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: no changed component in the deploy set has an empty description. For the flows you touched, read `Description` on `FlowDefinitionView`.
