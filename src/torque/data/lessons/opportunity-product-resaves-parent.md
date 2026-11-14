---
id: L-20261114-5e1c
title: Saving an Opportunity Product saves its Opportunity again
scope: platform
kind: fact
state: active
when: you are about to load or update Opportunity Products, or to add automation on Opportunity in an org that uses products
do: measure the CPU time of a small batch in a debug log first, and run one-time updates in batches of 50 or fewer when Opportunity automation is heavy
because: each line item save recalculates the parent's amount, which saves the Opportunity and runs its flows and triggers once per line
applies:
  tasks: [data-load]
  types: []
  commands: []
  keywords: [Opportunity Product, OpportunityLineItem, Opportunity, CPU time]
  errors: []
evidence:
  - {type: session, ref: "field-reported, one org (one consultant's working notes): 2 notes (100 Opportunity saves took 7 to 13 seconds of CPU in one org)", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: the debug log's CPU time for a batch of 50 line items stays well under the 10,000 ms synchronous limit.
