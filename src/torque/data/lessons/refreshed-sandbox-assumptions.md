---
id: L-20261114-b20c
title: After a sandbox refresh, check endpoints, scheduled jobs and embedded usernames first
scope: platform
kind: pitfall
state: active
when: you are about to work in a sandbox that was just refreshed
do: check the integration endpoints, the scheduled jobs and any run-as usernames before you rely on them; assume custom settings carry production values and that assignments made only in the sandbox are gone
because: a refresh copies production configuration and drops what was set up only in the sandbox
applies:
  tasks: [sandbox-refresh, sandbox, integration, scheduling]
  types: []
  commands: []
  keywords: [CronTrigger, custom settings, endpoint, run-as user]
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
Check: the integration endpoints, the `CronTrigger` rows and the usernames embedded in automation.
