# Review checklist: forty rules by phase

Use the rows for the phase under review. Each rule is a question to put to the design or the
build; the last column says where the detail and the checks live. State described:
Winter '27 (API 68.0), checked 2026-11-14. The rules are a synthesis of Salesforce's
Well-Architected pillars, decision guides and pattern libraries (sources at the end).

## Designing

| # | Rule | Detail |
| --- | --- | --- |
| 1 | The org's shape was established first: product family and installed packages, automation already on the objects in scope, sharing model, data volumes | `salesforce-delivery/references/assess-and-discover.md` |
| 2 | Reuse before build, in this order: standard feature, installed package, declarative, code. The options rejected are recorded | `salesforce-build/references/choosing-the-tool.md` |
| 3 | The design names the trade-off it made between pillars and is proportionate to the org | `decision-methods.md` |
| 4 | It is designed within limits, with headroom: 200 records per transaction, bulk loads | `salesforce-build/references/apex-and-lwc.md` |
| 5 | When a new constraint appeared, the whole trade-off was re-run, and the choice went to the accountable person | `decision-methods.md` |

## Changing the data model

| # | Rule | Detail |
| --- | --- | --- |
| 6 | No standard or package object or field is duplicated; in a nonprofit org the product's model is used | `salesforce-build/references/data-model.md` |
| 7 | The relationship type is chosen on purpose and per-object limits were checked | `salesforce-build/references/choosing-the-tool.md` |
| 8 | Every new field ships with a description, a classification and a permission-set grant; it is required only with a recorded reason | `salesforce-build/references/access-and-sharing.md` |
| 9 | Irreversible moves were stopped for: Person Accounts, picklist promotion, field type change, field deletion, relationship conversion, encryption | `salesforce-build/references/data-model.md` |
| 10 | Loaded or integrated objects have an external Id; selectivity and skew were checked on large objects | `salesforce-build/references/data-model.md` |

## Building automation

| # | Rule | Detail |
| --- | --- | --- |
| 11 | What already runs on the object was counted, and its existing entry point is used | `salesforce-flow/references/build-checklist.md` |
| 12 | Before-save for changes to the triggering record; after-save only for other records and actions | `salesforce-flow/references/build-checklist.md` |
| 13 | Entry conditions on the fields that matter; logic that gives the same result when repeated; trigger order set | `salesforce-flow/references/build-checklist.md` |
| 14 | Nothing queries or writes inside a loop; tested with 200 or more records | `salesforce-flow/references/build-checklist.md` |
| 15 | A fault path on every data operation and callout, with a message, a log and a monitored recipient | `salesforce-flow/references/build-checklist.md` |
| 16 | Nothing new in Workflow Rules or Process Builder; no hard-coded Ids; a bypass only through a custom permission | `salesforce-flow/references/build-checklist.md` |
| 17 | No Batch or scheduled Apex started from a trigger; a Queueable from a trigger is treated as a risk | `salesforce-build/references/apex-and-lwc.md` |

## Granting access

| # | Rule | Detail |
| --- | --- | --- |
| 18 | Least privilege through permission sets and groups, never profile edits; no Modify All Data or View All Data | `salesforce-build/references/access-and-sharing.md` |
| 19 | No org-wide default was loosened and no broad or guest sharing rule added without approval; the narrowest mechanism is used | `salesforce-build/references/access-and-sharing.md` |
| 20 | Each integration and agent has its own minimal identity; never an administrator or a shared API user | `salesforce-build/references/access-and-sharing.md` |
| 21 | Secrets live only in named and external credentials; never printed, committed or stored elsewhere | `salesforce-build/references/integration.md` |
| 22 | Configuration, assignment and effective access were checked separately, the last as the intended user | `salesforce-build/references/access-and-sharing.md` |

## Writing code

| # | Rule | Detail |
| --- | --- | --- |
| 23 | Every class states its sharing and every query and DML its access mode; no API version changed in passing | `salesforce-build/references/apex-and-lwc.md` |
| 24 | One trigger per object, handing over to a handler and a service; on a package-owned object, the package's framework | `salesforce-build/references/apex-and-lwc.md` |
| 25 | Queueable with a finalizer by default, Batch for volume, no `@future`; asynchronous work is safe to run twice | `salesforce-build/references/apex-and-lwc.md` |
| 26 | Components use Lightning Data Service and base components first, Apex last; wire errors are handled; the page is accessible | `salesforce-build/references/apex-and-lwc.md` |
| 27 | Tests assert behaviour: positive, negative, bulk, restricted user, own data | `salesforce-build/references/apex-and-lwc.md` |
| 28 | The code analyzer ran and high severities were fixed before deploying; no exception is swallowed | skill `salesforce-code-analyzer-review` |

## Integrating

| # | Rule | Detail |
| --- | --- | --- |
| 29 | The pattern was picked from the matrix, with the highest-ranked tool for it; legacy tools avoided | `salesforce-build/references/integration.md` |
| 30 | Writes can be repeated (upsert on an external Id); retries are bounded with growing waits; no blind retry of a write that is not repeatable | `salesforce-build/references/integration.md` |
| 31 | Explicit timeouts; no callout after uncommitted DML; composite response bodies are read; API allocations respected | `salesforce-build/references/integration.md` |
| 32 | Sandboxes never reach production endpoints; endpoints and switches are held outside the code | `salesforce-build/references/integration.md` |

## Deploying

| # | Rule | Detail |
| --- | --- | --- |
| 33 | The real target org was resolved, current metadata retrieved first, and newer work is not overwritten | `salesforce-delivery/references/environments-and-release.md` |
| 34 | Validated with tests before the real deployment; small, scoped changes from reviewed source | `salesforce-delivery/references/environments-and-release.md` |
| 35 | The way back exists before going forward: previous version, a switch, exact prior data; what cannot be undone is known | `salesforce-delivery/references/environments-and-release.md` |
| 36 | Peak and period-end windows are avoided; no automation is activated silently | `salesforce-flow/references/activation-and-deploy.md` |
| 37 | No package component is edited or copied; package boundaries and pending release updates were checked | `salesforce-build/references/gotchas.md` |

## Verifying

| # | Rule | Detail |
| --- | --- | --- |
| 38 | Behaviour was verified, not deployment status: the active flow version, effective access as the end user, the bulk path, the fault path | `salesforce-delivery/references/testing-and-uat.md` |
| 39 | Someone watches afterwards: error mail, failed interviews, failed jobs, limit use | `salesforce-delivery/references/handover-and-support.md` |
| 40 | The report says what was checked, how, and what was not; which checks were synthetic and which live | `salesforce-delivery/references/working-practice.md` |

## Stop-and-ask lists

A design that does any of these without a recorded yes from the accountable person is a
finding in itself.

| Area | Stop and ask before |
| --- | --- |
| Data model | Enabling Person Accounts or any irreversible org feature; converting a relationship; changing a field type or deleting a field; promoting a picklist to a global value set; a required field or validation rule on an object integrations or packages write to; encrypting a field used in filters or formulas; an object that overlaps a standard or package object |
| Access | Changing any org-wide default, role hierarchy or sharing rule; granting Modify All, View All, Author Apex, Manage Users or API access; a guest sharing rule or guest permission; `without sharing` or system-mode access; raising Apex API versions; creating or rotating credentials; turning on encryption |
| Automation | Activating or deactivating automation in production; adding automation to an object already at medium or high density or with mixed entry points; anything that emails, texts or calls out to people outside the org; creating or changing scheduled jobs; introducing or using a bypass |
| Pages | Changing page assignments or default apps for all users; removing a field or action people or integrations rely on; changing a dashboard's running user; exposing fields or flows on a public page |
| Integration | Any new flow of personal or regulated data out of the org; creating credentials or app registrations; widening scopes; pointing anything at a production endpoint; choosing a custom build over an existing connector |
| Data | Any mass update, merge or delete; activating a duplicate rule that blocks; deactivating rules or automation for a load; archiving or deleting data; a production load without a reconciliation plan |
| Org and release | Creating, refreshing or deleting a sandbox; installing, upgrading or removing a package; enabling an org-wide feature that cannot be turned off; deploying to production; any change with no tested way back |
| AI on the platform | Giving any agent write access to production; an action that writes, pays, emails or deletes; sending record data to a model outside the platform's trust layer |

## Sources

- Well-Architected framework and pillars: https://architect.salesforce.com/docs/architect/well-architected/guide/framework.html
- Pattern and anti-pattern libraries: https://architect.salesforce.com/docs/architect/well-architected/guide/patterns-and-anti-patterns.html
- Decision guides: https://architect.salesforce.com/decision-guides
- Record-triggered automation: https://architect.salesforce.com/docs/architect/decision-guides/guide/record-triggered
- Platform sharing architecture: https://architect.salesforce.com/docs/architect/fundamentals/guide/platform-sharing-architecture
- Integration patterns: https://architect.salesforce.com/docs/architect/fundamentals/guide/integration-patterns
- Salesforce Apex skill for agents: https://github.com/forcedotcom/sf-skills/blob/main/skills/platform-apex-generate/SKILL.md
