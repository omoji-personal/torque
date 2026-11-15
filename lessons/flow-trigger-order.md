---
id: L-20261114-5e19
title: Set the trigger order when you add a record-triggered flow to an object that already has automation
scope: platform
kind: pitfall
state: active
when: you are about to add or activate a record-triggered flow on an object that already runs flows, triggers or approvals
do: list what already runs on the object, set triggerOrder on the new flow, and after the deploy rerun one existing path end to end (an approval, a sync)
because: a new flow can change the order in which the object's automation runs, so an existing path sees other values and fails without an error
applies:
  tasks: [flow-build]
  types: [Flow]
  commands: []
  keywords: [triggerOrder, Flow Trigger Explorer, record-triggered, run order]
  errors: []
evidence:
  - {type: session, ref: "field-reported, a review of 166 rework episodes in 10 engagements, orgs per lesson not counted: 1 escaped defect: a new before-save flow stopped approvals for seven hours", date: 2026-11-14}
  - {type: session, ref: "field-reported, one org (one consultant's working notes): 3 notes on automation found late on the object", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: the new flow file carries `<triggerOrder>`, and the existing path gives the same outcome after the deploy as before it.
