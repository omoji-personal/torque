---
id: L-20261114-5e25
title: Load one record first, then read what it did
scope: platform
kind: works
state: active
when: you are about to load, import or bulk update records in any org
do: load one record, or a few, first; read what they fired (automation, background jobs, email, integrations) and what they changed, and only then load the rest
because: a load runs every automation on the object for every row, and a mistake found on one record costs one record
applies:
  tasks: [data-load]
  types: []
  commands: ["sf data * bulk*", "sf data import tree*"]
  keywords: [import, Data Loader, bulk]
  errors: []
evidence:
  - {type: official-doc, ref: "https://trailhead.salesforce.com/content/learn/projects/import-your-data-using-npsp-data-importer/prepare-your-data-for-import", date: 2026-11-14}
  - {type: session, ref: "field-reported, a review of 166 rework episodes in 10 engagements, orgs per lesson not counted: 10 episodes in which test or loaded records had side effects", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: the first record's jobs, emails and changed fields are read and noted before the next batch.
