# Failure modes that cost the most, and what prevents each

Read this when you plan a phase or review a plan: find the rows for the phase and check the
plan holds the prevention. The 26 rows come from Salesforce, community and standards-body
sources (tags at the end); the last column says where this playbook carries the practice.
field-reported = also seen in the field record behind these skills.

| # | Failure | What prevents it | Source | Read |
| --- | --- | --- | --- | --- |
| 1 | Work or a write lands in the wrong org | Resolve the org's identity first; name the target org on every command; never rely on a default org | [MCP] | `assess-and-discover.md`, A1 |
| 2 | A deploy overwrites someone else's change in a shared sandbox or production | Retrieve and compare right before deploying; the CLI warns of conflicts only where source is tracked | [PLUGIN-DR] | `environments-and-release.md`, A3 |
| 3 | A fix made by hand in production disappears at the next deployment | Route a hotfix through source and carry it back | [HARDIS-HOTFIX] | `environments-and-release.md`, A2 |
| 4 | A sandbox refresh destroys unfinished work | Approval from everyone affected; commit first; delta backup | [TH-SBX] | `environments-and-release.md`, A4 |
| 5 | A sandbox emails real people | Post-refresh runbook with email Deliverability first; undeliverable test addresses | [TH-SBX] | `environments-and-release.md`, A4 |
| 6 | UAT is signed off without real coverage, or by the wrong testers | Scripts written with the people who do the job, per persona; written sign-off | [TH-UAT] | `testing-and-uat.md`, section 5 |
| 7 | The team builds what was asked, not what was needed | Ask why; restate the request and get a yes; options with trade-offs | [TH-ADV] | `assess-and-discover.md`, B2 |
| 8 | Tests exist for coverage and assert nothing | Positive, negative and bulk cases; review generated tests for assertions | [TH-TEST] | `salesforce-build/references/apex-and-lwc.md` |
| 9 | A flow ships untested because nothing runs flow tests | Run flow tests from the CLI; test each path by hand where no test exists | [PLUGIN-APEX] | `salesforce-flow/references/build-checklist.md` |
| 10 | Automation is switched off for a load and never restored, or totals are never rebuilt | Bypass list with restore steps; recalculation jobs; a before-and-after list of what is active | [TH-LOAD] | `data-migration.md`, section 4 |
| 11 | Dirty and duplicate data is migrated; preparation was not in the estimate | Profile, clean and de-duplicate first; budget for it | [TH-PREP] | `data-migration.md`, section 1 |
| 12 | Large loads fail on record locks | Sort children by parent; rehearse at volume; no batch jobs during loads | [TH-SCALE] | `data-migration.md`, section 3 |
| 13 | A release goes out partly applied or unvalidated, with no way back | Validate, then deploy the validated job; never ignore errors in production; keep the prior version | [PLUGIN-DR] | `environments-and-release.md`, C |
| 14 | Manual steps are forgotten in one environment | A manual-steps register, ticked per org | [HARDIS-STEPS] | `environments-and-release.md`, C1 |
| 15 | A seasonal release, an enforced update or an API retirement breaks the client | The readiness routine in a preview sandbox, three times a year | [TH-RELTEST] | `handover-and-support.md`, D |
| 16 | Deadline shortcuts and mixed automation become permanent debt | Decision tables; one entry mechanism per object; a debt register | [DG-RT] | `salesforce-build/references/choosing-the-tool.md` |
| 17 | Custom is built where standard would do; maintenance cost is ignored | Native baseline first; options with cost over three to five years | [WA-RC] | `design-and-estimate.md`, A1 |
| 18 | Stale or over-privileged consultant access, or an approved rogue app, leads to data theft | Least privilege, multi-factor sign-in, an end date on access, verify any app before approving it | [GTIG] | `handover-and-support.md`, A1 step 12 |
| 19 | Credentials sit in records, notes or repositories and are harvested | No secrets in code or notes; scan and rotate; never print tokens | [GTIG-2] | `salesforce-build/references/integration.md` |
| 20 | No backup, or one never restored, when data is damaged | Confirm the backup at the start; test a restore each quarter | [WA-R] | `handover-and-support.md`, C |
| 21 | Scope grows without a decision | Scope baseline under version control; each change request decided before work | [APM] | `design-and-estimate.md`, B1 |
| 22 | The system works and nobody uses it | Adoption defined as use of the process, with a number and a date | [TH-LEVERS] | `handover-and-support.md`, A1 |
| 23 | An AI-generated change is deployed unreviewed, or is too large to review | A person reviews before deploy; the same checks as for human work; small batches | [DORA] | `working-practice.md`, section 8 |
| 24 | Client personal data enters an unapproved AI tool or a sandbox | The client's agreement first; restricted data kept out of non-production | [GDPR-28] | `data-migration.md`, section 7 |
| 25 | Knowledge leaves with one consultant | Decisions, procedures and the build register in the client's records; a hand-off note | [COE] | `handover-and-support.md`, A4 |
| 26 | A quick fix of security settings breaks integrations | Read Health Check during assessment; change one setting at a time after a sandbox test | [TH-HC] | `assess-and-discover.md`, A1 |

## Seen in the field record as well

Field-reported, from an after-action review of 166 rework and defect episodes (one
consultant, about a dozen clients):

| Failure | Count | Prevention |
| --- | --- | --- |
| Work recorded as verified and later contradicted | 28 of 92 records | Say what the check rested on (`working-practice.md`, section 1) |
| An org fact stated from a sandbox copy or from metadata alone was wrong in production | 28 episodes, 8 seen by a client | Org and date on every fact (`working-practice.md`, section 2) |
| A comment or message posted too early, then edited | 22 episodes, 14 seen by a client | One vet, then post once (`working-practice.md`, section 6) |
| Work built on a purpose nobody confirmed | 17 episodes | Restate the request and get a yes |
| A rule that did not fit the real variety of the data | 14 episodes | Aggregate query of real shapes first (`testing-and-uat.md`, section 2) |
| A change that worked for the admin and not for the user | 13 episodes | Test as the real user (`testing-and-uat.md`, section 3) |
| A design replaced by something the platform already offered | 12 episodes | Options with hours before design (`working-practice.md`, section 5) |
| A test that sent something outside the org or left data behind | 10 episodes, 21 leftover sets | Outbound check and a leftover ledger (`testing-and-uat.md`, section 4) |
| A fix that broke the neighbouring path | 8 episodes | Rerun the journey and its neighbour |

## Sources

- [MCP] Access model of Salesforce's retired local MCP server (explicit org names, not a default): https://raw.githubusercontent.com/salesforcecli/mcp/main/README.md
- [PLUGIN-DR] https://raw.githubusercontent.com/salesforcecli/plugin-deploy-retrieve/main/README.md
- [PLUGIN-APEX] https://raw.githubusercontent.com/salesforcecli/plugin-apex/main/README.md
- [HARDIS-HOTFIX] https://sfdx-hardis.cloudity.com/salesforce-devops-hotfixes/
- [HARDIS-STEPS] https://sfdx-hardis.cloudity.com/salesforce-devops-work-on-user-story-deployment-actions/
- [TH-SBX] https://trailhead.salesforce.com/content/learn/modules/nonprofit-success-pack-maintenance/set-up-a-sandbox-in-your-salesforce-org
- [TH-UAT] https://trailhead.salesforce.com/content/learn/modules/user-acceptance-testing-video/learn-about-user-acceptance-testing
- [TH-ADV] https://trailhead.salesforce.com/content/learn/modules/business-analyst-best-practices/advocate-for-the-customer
- [TH-TEST] https://trailhead.salesforce.com/content/learn/modules/apex_testing/apex_testing_intro
- [TH-LOAD] https://trailhead.salesforce.com/content/learn/modules/large-data-volumes/load-your-data
- [TH-PREP] https://trailhead.salesforce.com/content/learn/projects/import-your-data-using-npsp-data-importer/prepare-your-data-for-import
- [TH-SCALE] https://trailhead.salesforce.com/content/learn/modules/org-scaling/mitigate-failing-data-loads
- [TH-RELTEST] https://trailhead.salesforce.com/content/learn/modules/advanced-salesforce-release-readiness-strategies/test-new-features-before-a-release
- [TH-LEVERS] https://trailhead.salesforce.com/content/learn/modules/levers-of-change-model/use-the-levers-model-to-diagnose-barriers-to-change
- [TH-HC] https://trailhead.salesforce.com/content/learn/modules/security_basics/security_basics_healthcheck
- [DG-RT] https://architect.salesforce.com/docs/architect/decision-guides/guide/record-triggered
- [WA-RC] https://architect.salesforce.com/docs/architect/well-architected/guide/resource-and-cost-optimization.html
- [WA-R] https://architect.salesforce.com/docs/architect/well-architected/guide/reliability.html
- [GTIG] https://cloud.google.com/blog/topics/threat-intelligence/voice-phishing-data-extortion
- [GTIG-2] https://cloud.google.com/blog/topics/threat-intelligence/data-theft-salesforce-instances-via-salesloft-drift
- [APM] https://www.apm.org.uk/resources/what-is-project-management/what-is-change-control/
- [DORA] https://dora.dev/capabilities/working-in-small-batches/
- [GDPR-28] https://gdpr-info.eu/art-28-gdpr/
- [COE] https://www.salesforce.com/blog/center-of-excellence-ai-governance/
- field-reported: an after-action review of 166 rework and defect episodes (one consultant,
  about a dozen clients, mostly sandboxes), cited by class and count only.
