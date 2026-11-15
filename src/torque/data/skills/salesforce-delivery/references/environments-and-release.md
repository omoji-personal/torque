# Environments, the build register, release and deployment

State described: Winter '27 (API 68.0), checked 2026-11-14. **verify** = not confirmed in a
fetched primary source: verify in the org or the current documentation. field-reported =
seen in client work, usually one org. `[HUMAN]` = a person decides or acts.

## A. Environments

### A1. Know what each org is

| Sandbox | Storage | Refresh every | Use | Tracks source |
| --- | --- | --- | --- | --- |
| Developer | 200 MB | 1 day | One builder's isolated build and test | Yes |
| Developer Pro | 1 GB | 1 day | QA, integration, training | Yes |
| Partial Copy | 5 GB | 5 days | UAT, integration, training | No |
| Full | All data | 29 days | Performance, load and staging only | No |

Sources: [SBX] [TH-ENV] [PLUGIN-ORG]. Full and Partial Copy sandboxes hold real production
data: "it is a sandbox" does not make a bulk change safe [ADV `sandbox-contains-real-data`].
Keep restricted data out of non-production, or mask it [WA-T]. Where source is not tracked
(Partial, Full, production) the CLI warns of no conflict: your deploy overwrites [PLUGIN-DR].

### A2. Checklist

1. Keep the environment register: org, type, purpose, last refresh, preview status, owner,
   data sensitivity, whether it tracks source.
2. Settle the source of truth, the org or the repository, and write it in the repository's
   README with the org-to-branch map [TH-MODEL].
3. Prefer your own Developer sandbox. In a shared one follow A3.
4. Before you touch an existing component, retrieve its current version from the target and
   compare it with the repository.
5. Run `sf project deploy preview --target-org ALIAS` before a deploy. Never pass
   `--ignore-conflicts` without a yes for that deploy.
6. Keep changes small and reviewed: a check that must pass before a merge, and a second
   approver for anything bound for production [WA-OE].
7. A fix made by hand in production disappears at the next deployment: route a hotfix through
   source and carry it back to the build branches (community practice [HARDIS-HOTFIX]).
8. `[HUMAN]` each refresh, each merge to a protected branch.

### A3. In a sandbox other people use

Field-reported rule from a shared staging sandbox: every deploy is additive.

1. Build from that org's own current retrieve, not from an older folder.
2. Re-retrieve each component right before you deploy and compare.
3. Deploy only the exact components, after a dry run.
4. Retrieve again and confirm the only differences are yours.
5. If the history shows a colleague editing the same component, stay read-only until they
   agree.

### A4. Sandbox refresh

A refresh "destroys any of the data or customizations you created in your sandbox since your
last refresh" [TH-SBX], gives the sandbox a new org Id [ADV `sandbox-refresh-changes-orgid`],
and `sf org refresh sandbox` activates the new copy unless `--no-auto-activate` is passed
[PLUGIN-ORG].

Before: announce it; confirm nothing is uncommitted; take a delta backup (list the metadata
changed since the sandbox was created, retrieve it); `[HUMAN]` approval from everyone who
could lose work.

After, in this order:

1. Email Deliverability to system email only, before anything can send [TH-SBX].
2. Integration endpoints and credentials: nothing points at production. Custom settings come
   back with production values (field-reported).
3. Re-authorize the CLI and confirm identity with an Organization query. If calls fail with
   "The requested resource does not exist", pass the API version explicitly (field-reported).
4. Users, permission set assignments and sign-in methods: sandbox-only ones are gone
   (field-reported).
5. Scheduled jobs: they do not come across (field-reported). Recreate the ones tests need.
6. Package settings, and any user name stored inside automation [TH-SBX].
7. Seed data, with undeliverable addresses.
8. Rerun behaviour checks. A restore "verified" by component names had lost public access
   to its endpoints, and a reinstalled package had re-activated a flow (field-reported):
   compare guest access and the active-flow list with before.

Before each Salesforce release, check which sandboxes are on the preview instance: a refresh
inside the preview window can move a sandbox off it [TH-PREVIEW].

## B. The build register

Enter every sandbox change the same day (field-reported: production deploys come weeks
later, and a register kept from memory misses components).

| Column | Content |
| --- | --- |
| Component | Type and API name, source file |
| Where | Sandbox, its state (deployed, active, assigned) |
| Deploys | Every deploy job id |
| Side effects | Data created or changed, temporary settings and their restore |
| Tests | What ran, as whom, what did not run |
| Production | What blocks it, the manifest (proven by a retrieve), the manual steps |

- Name one current source folder per build. Fixes made in the org during QA go back into
  that source the same day.
- "Done" for a story means: criteria met, tests, analysis clean or exceptions written,
  descriptions present, reviewed, documented, registered.

## C. Release and deployment

### C1. Checklist

1. Take the scope from the build register; confirm each item passed UAT.
2. Build the manifest for exactly those components, never the whole source folder. Put
   deletions in destructive manifests, before or after the deploy as the dependencies need.
3. Complete the manual-steps register: each step, before or after, owner, how it is
   verified. Some settings cannot be deployed and must be made by hand in each org.
4. Retrieve the current production version of every component in scope. Compare it with the
   baseline you built from (drift). Keep it: it is the rollback package [WA-OE].
5. Confirm the data backup and how a restore would work. Before a consequential data change,
   save the exact prior values: a count is not a backup [WA-OE].
6. Validate against production and record the job id:
   `sf project deploy validate --manifest package.xml --target-org ALIAS` [PLUGIN-DR].
7. `[HUMAN]` the client approver gives go or no-go. Pick a low-traffic window, not a period
   end and not just before a weekend; tell users what changes and when [WA-OE].
8. Deploy the validated job: `sf project deploy quick --job-id JOB_ID --target-org ALIAS`.
   The job id is valid for 10 days. Validate again if production changed since.
9. Do the post steps. Deployed is not active: activate flows, assign permission sets,
   activate pages, each as designed, each read back.
10. Smoke-test as real personas, check where error mail goes, and watch errors and failed
    jobs for a day or two.
11. Compare production with the repository and file the evidence with the job id
    (`sf project deploy report --job-id JOB_ID --target-org ALIAS`).
12. Write release notes: what users will see, technical changes, known issues, actions.
13. Merge, tag, carry back any hotfix, close the register rows.

### C2. Test levels and flags

| Test level | Meaning [PLUGIN-DR] |
| --- | --- |
| `NoTestRun` | Development environments only; the default there. `deploy validate` does not offer it |
| `RunSpecifiedTests` | Only the tests named with `--tests`; each class and trigger in the deployment needs at least 75% coverage, counted per class, not across the org |
| `RunLocalTests` | Default for a production deployment that contains Apex classes or triggers, and for `deploy validate`; excludes package tests |
| `RunAllTestsInOrg` | Everything, package tests included |
| `RunRelevantTests` | Beta: the platform picks tests from the payload; the same 75% per class and trigger |

Without `--test-level`, the default depends on the payload and the target org [PLUGIN-DR].
For a sandbox use `sf project deploy start --dry-run`; `deploy validate` is meant for
production [PLUGIN-DR]. Treat these flags as approvals, not conveniences: `--ignore-errors`
(never on production), `--ignore-conflicts`, `--ignore-warnings`, `--purge-on-delete`
(sandboxes and Developer Edition only, **verify**).

### C3. The way back

- A deployment that succeeded has no undo. The way back is a redeploy of the version you
  kept in C1, step 4, a flag you can switch off, or exact prior data.
- A redeploy does not restore deleted fields and their data, changed or merged records, sent
  email, callouts already made or replaced picklist values.
- Ship a risky change switched off behind a custom metadata flag or a custom permission, and
  turn it on for a small group first [P-DEVOPS].

### C4. Field lessons

- Write the production runbook before the go: the gate (open questions answered, tested as a
  real staff user, the consultant's go), a read-only drift check, the exact manifest, the
  activation order, the checks and the rollback.
- A validation rule deploys with the `active` value in its file, in production too
  (**verify**: read the rule's Active flag after the deploy). Where it
  could block live users or integrations, deploy it to production with
  `<active>false</active>` and activate it as its own approved step.
- For a production setting changed by hand: record the prior value, change one thing at a
  time, read the form back before Save, re-read from the org after, keep the revert.
- Exit code 0 is not proof of effect: an invalid flag printed help and exited 0, and nothing
  had deployed. Track the exact job id and read the result.

## D. Deploy and retrieve mechanics

Field-reported, from 273 failed Salesforce CLI calls in one consultant's work:

- **Check-only before every real deploy.** Check-only runs caught 28 of 36 deploy failures,
  and 7 of the 8 real deploys that failed had no check-only run just before them.
- **A project folder, or the metadata-format flags.** `--source-dir` and `--output-dir` need
  an `sfdx-project.json` whose package directory exists, with the output inside it. Outside
  a project, retrieve with `--target-metadata-dir DIR --unzip` and deploy with
  `--metadata-dir DIR` (23 failures in 6 sessions).
- **A flow retrieved at a newer API version than the project's failed** with "Property X
  not valid in version N". Pass `--api-version` for that retrieve or deploy; change
  `sourceApiVersion` in `sfdx-project.json` only as its own tested change, never down for
  one file (`salesforce-flow/references/gotchas.md`).
- **Write `--json` output to a file and read the file.** Do not pipe a Salesforce command
  through a filter or merge its error stream into the file: a pipe hid the failure of 44% of
  failed calls, and a warning line in front of the JSON broke the read.
- **A deploy that ends with no reason shown** needs `sf project deploy report --job-id`.
- **Prune a broad retrieve before you redeploy it**: it holds members the platform created
  that cannot be deployed (2 failed deploys after a refresh).
- **A retrieve that reports "cannot be found" returned a partial result**: fix the member
  name and run it again.
- **Let a long retrieve or test run finish** (`--wait`); do not start a second copy.

Seasonal platform releases and package releases: `handover-and-support.md`, section D.

## Artifacts

| Artifact | Minimum contents |
| --- | --- |
| Environment register | A2, step 1 |
| Post-refresh runbook | A4, with owner and verification per step |
| Deployment runbook | Prerequisites, manifest, order, validation job id, steps, smoke tests, rollback |
| Manual-steps register | Step, before or after, org, owner, done per org, verification |
| Release notes | User-facing changes, technical changes, known issues, actions needed |

## Sources

- [SBX] https://www.salesforce.com/platform/sandboxes-environments/pricing/
- [TH-ENV] https://trailhead.salesforce.com/content/learn/modules/environments-for-developers/explore-available-developer-environments
- [TH-MODEL] https://trailhead.salesforce.com/content/learn/modules/sfdx_dev_model/sfdx_dev_model_neworganization
- [TH-SBX] https://trailhead.salesforce.com/content/learn/modules/nonprofit-success-pack-maintenance/set-up-a-sandbox-in-your-salesforce-org
- [TH-PREVIEW] https://trailhead.salesforce.com/content/learn/modules/sf_releases/sf-releases-sandbox-preview
- [PLUGIN-ORG] https://raw.githubusercontent.com/salesforcecli/plugin-org/main/README.md
- [PLUGIN-DR] https://raw.githubusercontent.com/salesforcecli/plugin-deploy-retrieve/main/README.md
- [WA-OE] https://architect.salesforce.com/docs/architect/well-architected/guide/operational-excellence.html
- [WA-T] https://architect.salesforce.com/docs/architect/well-architected/guide/trust.html
- [P-DEVOPS] https://architect.salesforce.com/docs/architect/well-architected/guide/operational-excellence-devops-patterns
- [HARDIS-HOTFIX] Community practice: https://sfdx-hardis.cloudity.com/salesforce-devops-hotfixes/
- [ADV] Torque advisory catalogue (`torque advisory notes --command TEXT`); entry ids inline.
- field-reported: lessons from real client work with Torque, including a classification of
  273 failed Salesforce CLI calls and an after-action review of 166 rework episodes (one
  consultant, mostly sandboxes). Not re-tested for this release.
