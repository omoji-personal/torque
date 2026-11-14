---
id: L-20261114-5e0c
title: A wildcard retrieve leaves out managed and packaged components
scope: platform
kind: pitfall
state: active
when: you are about to retrieve with a wildcard manifest for a baseline or a study
do: name standard and managed objects explicitly, put the namespace inside the names of managed layouts, and compare the number of files with a metadata listing
because: a manifest wildcard returns only unmanaged custom objects, a wildcard Apex retrieve skips unlocked-package code, and a managed layout named without its namespace returns almost nothing, all without an error
applies:
  tasks: [retrieve]
  types: [CustomObject, Layout, ApexClass]
  commands: ["sf project retrieve start*--manifest*"]
  keywords: [wildcard, manifest, namespace, baseline]
  errors: []
evidence:
  - {type: session, ref: "field-reported, one org (one consultant's working notes): 3 notes", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: `sf org list metadata --metadata-type TYPE --target-org ALIAS` lists as many components as the retrieve wrote files.
