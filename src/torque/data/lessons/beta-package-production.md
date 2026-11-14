---
id: L-20261114-5e24
title: Check that a managed package version is not a beta before you plan it for production
scope: platform
kind: pitfall
state: active
when: you are about to plan a production install or upgrade of a managed package version
do: query `SubscriberPackageVersion.IsBeta` on `InstalledSubscriberPackage` through the Tooling API for each version the plan names
because: a beta version cannot be installed in production or upgraded in place, and `sf package installed list` does not show the flag
applies:
  tasks: [packaging, production]
  types: [InstalledPackage]
  commands: ["sf package install*", "sf package installed list*"]
  keywords: [IsBeta, beta, SubscriberPackageVersion]
  errors: []
evidence:
  - {type: session, ref: "field-reported, one org (one consultant's working notes): 2 notes (a specification named a beta version for production)", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: `sf data query --use-tooling-api --query "SELECT SubscriberPackage.Name, SubscriberPackageVersion.IsBeta FROM InstalledSubscriberPackage" --target-org ALIAS` shows IsBeta false for each version.
