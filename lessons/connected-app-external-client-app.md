---
id: L-20261114-5e0a
title: New connected apps cannot be created; use an external client app
scope: platform
kind: fact
state: active
when: you are about to create a connected app for an integration, or a deploy fails with "You can't create a connected app"
do: create an external client app for the integration instead, and leave the org's existing connected apps as they are
because: new connected app creation is disabled from Spring '26, and integrations are governed through external client apps
applies:
  tasks: [integration]
  types: [ConnectedApp]
  commands: []
  keywords: [external client app, OAuth]
  errors: ["You can*t create a connected app"]
evidence:
  - {type: official-doc, ref: "https://architect.salesforce.com/docs/architect/well-architected/guide/trust.html", date: 2026-11-14}
  - {type: session, ref: "field-reported, one consultant's CLI transcripts over 2.5 weeks: 1 failed deploy", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: the integration authorizes through the external client app in a sandbox.
