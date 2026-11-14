---
id: L-20261114-5e08
title: Descriptions of profiles, permission sets and validation rules hold 255 characters
scope: platform
kind: pitfall
state: active
when: a deploy fails with "cannot be longer than 255" or "data value too large", or you are about to write a long description on a profile, permission set or validation rule
do: shorten the value to the length the message names; keep the descriptions of profiles, permission sets and validation rules within 255 characters
because: one value over the limit fails the whole deploy, and these types allow less than a field description does
applies:
  tasks: [deploy, documentation]
  types: [Profile, PermissionSet, ValidationRule]
  commands: []
  keywords: [description, length limit]
  errors: ["cannot be longer than 255", "data value too large"]
evidence:
  - {type: session, ref: "field-reported, one consultant's CLI transcripts over 2.5 weeks, orgs not counted: 3 failed deploys in 2 sessions; the limit as the error text states it", date: 2026-11-14}
  - {type: session, ref: "field-reported, a review of 166 rework episodes in 10 engagements, orgs per lesson not counted: 4 failed deploys in 3 engagements", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: `torque check metadata` reports no `doc.too-long` finding for the components in the deploy.
