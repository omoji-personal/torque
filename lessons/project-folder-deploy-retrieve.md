---
id: L-20261114-5e07
title: Source-format deploy and retrieve need a project folder; outside one, use the metadata-format flags
scope: platform
kind: pitfall
state: active
when: a retrieve or deploy fails because the folder is not a DX project, a package directory is missing, or the output folder is outside the project
do: run from a project root whose sfdx-project.json lists an existing package directory; for a scratch copy outside a project, retrieve with `--target-metadata-dir DIR --unzip` and deploy with `--metadata-dir DIR`
because: "`--source-dir` and `--output-dir` work only inside a DX project, with the output inside it"
applies:
  tasks: []
  types: []
  commands: ["sf project retrieve start*--output-dir*"]
  keywords: [sfdx-project.json, target-metadata-dir, package directory]
  errors: ["specified in sfdx-project.json*does not exist", "must be inside the current project", "does not contain a valid Salesforce DX project"]
evidence:
  - {type: session, ref: "field-reported, one consultant's CLI transcripts over 2.5 weeks, orgs not counted: 23 failed deploy and retrieve commands in 6 sessions", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: `sfdx-project.json` is in the working folder or above it, and its package directory exists, before the command runs.
