---
id: L-20261114-c24e
title: Before test records in a sandbox, check what can leave it
scope: platform
kind: pitfall
state: active
when: you are about to create or update people, gifts or other test records in a sandbox
do: check first what can leave the sandbox (email Deliverability, text gateways, connector error alerts, integration endpoints and their fallbacks, marketing fields), and make test addresses undeliverable
because: a sandbox can send real messages to real people and post to live integrations
applies:
  tasks: [sandbox, testing, data-load, email, integration]
  types: []
  commands: []
  keywords: [Deliverability, test data, outbound]
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
Check: the Deliverability setting; after the first test record, the day's background jobs show nothing outbound.
Not covered: Deliverability set to system email only does not stop a vendor's own email, text messages or outbound connectors; check each integration on its own side.
