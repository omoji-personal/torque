---
id: L-20261114-5e0d
title: Scan retrieved custom metadata and settings for secrets before you open them
scope: platform
kind: pitfall
state: active
when: you are about to open or print custom metadata records, custom settings, named credentials or sample payloads retrieved from an org
do: scan the retrieved copy for secret-looking values first and print only key names and hosts; report a secret by file and field, never by its value
because: these files can hold live credentials, also in fields with harmless names, and whatever is printed enters the session
applies:
  tasks: [retrieve]
  types: [CustomMetadata, NamedCredential]
  commands: []
  keywords: [custom settings, sample request, secret, credential]
  errors: []
evidence:
  - {type: session, ref: "field-reported, one org (one consultant's working notes): 5 notes", date: 2026-11-14}
  - {type: session, ref: "field-reported, a review of 166 rework episodes in 10 engagements, orgs per lesson not counted: 6 privacy slips, one a credential fragment printed from integration metadata", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: `torque check metadata` on the retrieved folder reports each `secret.looks-like-secret` finding by file and key only.
