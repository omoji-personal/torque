# Anti-pattern catalogue

Forty smells Salesforce's own guidance names, with how to detect each and the better
pattern. Use it as a checklist in a review, or to explain a finding. State described:
Winter '27 (API 68.0), checked 2026-11-14. The patterns come from fetched Salesforce pages
(tags at the end). The detection queries and element names are leads: written from the
research, not run for this release. Confirm names with describe, run them read-only, and
treat an error as "not checked". "J" = judgement only.

| # | Smell | Detect | Better pattern | Source |
| --- | --- | --- | --- | --- |
| A01 | Public Read/Write org-wide default set for convenience | `EntityDefinition.InternalSharingModel` | Private, then deliberate sharing | [WA-T] [P-IAM] |
| A02 | Profiles cloned for small variations and carrying functional permissions | Profile count; users per profile; profile metadata | A minimal profile plus permission sets and groups, with muting | [P-IAM] [F-SHARE] |
| A03 | Modify All Data or View All Data without a recorded reason | `PermissionSetAssignment` where `PermissionSet.PermissionsModifyAllData = true` | Object-level permissions, least privilege | [P-IAM] |
| A04 | One shared "API user" with broad rights for several integrations | Integration users; OAuth usage by app | One integration user, permission set and app registration each | [P-IAM] |
| A05 | Credentials in Apex, flows, custom settings, custom metadata or the repository | Secret scan; search for `Authorization`, `client_secret` | Named and external credentials | [P-DATA] [P-IAM] |
| A06 | Username-password sign-in flows or SOAP login in integrations | Setup > Connected Apps OAuth Usage; login history | JWT bearer or client credentials through an external client app | [WA-T] [REL-W27] |
| A07 | Apex with no sharing keyword, or data access in system mode by default | Search classes; Code Analyzer | Explicit sharing and access mode | [P-IAM] [REL-S26] |
| A08 | SOQL built by joining input into a string | Code Analyzer; search `Database.query(` | Bind variables | [P-SDEV] [WA-T] |
| A09 | Guest users with create, update or delete; broad guest sharing rules; system-context flows on public pages | Guest profile; guest sharing rules; flow run mode | Minimal read or create, narrow rules, user context | [P-SDEV] [DG-FORMS] |
| A10 | Sandboxes with unmasked production data or production endpoints, or left stale and shared | Sandbox list; credential addresses | Masked or synthetic data, test endpoints, an owner per sandbox | [P-DATA] [P-DEVOPS] |
| A11 | Health Check findings ignored, score under 80% | Tooling `SecurityHealthCheck` | Quarterly review, each exception written down | [P-COMP] |
| A12 | Several triggers on one object, or business logic in the trigger body | `ApexTrigger` grouped by object; trigger length | One thin trigger, a handler, a service layer | [P-AUTO] [P-CODE] |
| A13 | Flow and Apex triggers mixed as entry points on one object by accident | Flow Trigger Explorer plus the trigger list | One entry point, or a documented order where a package forces the mix | [DG-RT] |
| A14 | Queries, DML or flow data elements inside loops | Code Analyzer; flow file | Collect, then one query and one write | [P-SCALE] [TH-FLOW] |
| A15 | An after-save flow or trigger that updates the triggering record's own fields | Flow trigger type with an update to the same record | A before-save update | [DG-RT] |
| A16 | A record-triggered flow with no entry conditions, or one flow holding everything | `start` without filters; element count | Change-based entry conditions, single-purpose flows | [DG-RT] [P-AUTO] |
| A17 | Trigger order left unset | `triggerOrder` absent | An order value on every record-triggered flow | [P-AUTO] |
| A18 | Flows without fault paths; default "unhandled fault" messages | `faultConnector` count; failed interviews | Fault paths, plain messages, logging | [P-AUTO] |
| A19 | Hard-coded Ids, record types, users or addresses | Search for 15 and 18 character Ids | Custom metadata, labels, named credentials, lookups by developer name | [P-AUTO] |
| A20 | New automation in Workflow Rules or Process Builder | Tooling `WorkflowRule`; process-type flows | Flow, and migrate what exists | [P-AUTO] [WA-RC] |
| A21 | Batch or scheduled Apex started from a trigger; unbounded Queueable enqueues | Search trigger handlers | A pending flag swept by a scheduled job, or an event subscriber | [DG-RT] [F-ASYNC] |
| A22 | `@future` as the general asynchronous tool | Search | Queueable with a finalizer | [F-ASYNC] [SK-APEX] |
| A23 | Asynchronous work used to dodge a limit, or where the user needs the answer | J | Fix the design; stay synchronous when it fits | [DG-ASYNC] [WA-RC] |
| A24 | Events published immediately without need; events chaining a team's own flows; a subscriber republishing its own event | `publishBehavior`; flows that publish and subscribe | Publish after commit; subflows or orchestration | [DG-EDA] [P-AUTO] |
| A25 | Swallowed exceptions, stack traces shown to users, sensitive data in debug logs | Search for empty catch blocks and debug statements | Handle, log with context, safe user messages | [P-AVAIL] [P-SDEV] |
| A26 | Tests written for coverage: no assertions, org data, real callouts, skipped tests | Search tests; test results | Behaviour tests: positive, negative, bulk, restricted user | [P-DEVOPS] |
| A27 | Transactions near their limits, no headroom | Debug log limit usage | A stated ceiling below the limit | [P-SCALE] [WA-R] |
| A28 | Non-selective queries, reports or list views on large objects | Query Plan | Indexed, selective filters | [P-AUTO] |
| A29 | Data skew unwatched: a parent or an owner above 10,000 records | Aggregate counts by parent and by owner | Spread the load; a skewed owner with no role | [WA-RC] [F-SHARE] |
| A30 | All history kept in active storage; Attachments where Files belong | Setup > Storage Usage; counts | Lifecycle tiers, archive, Files | [P-STOR] [WA-RC] |
| A31 | Integrity enforced only in triggers; fields required without a reason | Validation rules against trigger checks; the required list | Validation and duplicate rules; a reason per required field | [P-AUTO] [WA-F] |
| A32 | Chatty, polling, record-by-record or point-to-point integrations | API usage by client | Bulk, composite, event-driven, a hub | [P-STOR] [WA-RC] |
| A33 | Outbound Messaging, Data Loader, Salesforce to Salesforce or PushTopic as the base of a new integration | Metadata and scheduled jobs | Events, Pub/Sub API, Bulk API 2.0, middleware | [DG-INT] [DG-EDA] |
| A34 | Callouts with no timeout or fallback; blind retries of a write that is not repeatable | Search `HttpRequest` | Timeouts, growing waits with a ceiling, repeat-safe writes | [P-AVAIL] [F-AGINT] |
| A35 | The org as the source of truth; manual or ad hoc deployments | Setup Audit Trail; drift between the org and the repository | Source-driven, reviewed, validated deployments | [P-DEVOPS] |
| A36 | No way back, or an untested one; big-bang, peak-hour or pre-weekend releases | J; a runbook and a rehearsal exist | A tested rollback, switches, staged exposure | [P-DEVOPS] |
| A37 | Backups never restored; reliance on platform backups or manual exports | Export schedule; date of the last restore test | An automated backup and a restore drill each quarter | [P-DR] [WA-R] |
| A38 | Custom pages people cannot use: placeholder as label, no focus outline, mouse only, low contrast | Accessibility tests; a keyboard walk-through | Base components, semantic HTML, tested focus and contrast | [WA-F] [P-A11Y] |
| A39 | An AI agent for a fixed path; human approval written as a prompt instruction; an agent running as an administrator | The agent user's permissions; its assigned actions | Flow or Apex for fixed paths; an approval step outside the agent; a least-privilege agent user | [DG-AGENT] [P-TAG] |
| A40 | Editing or copying package components, or rebuilding package logic (a second rollup engine, triggers that fight the package) | `ManageableState`; duplicate developer names; overlapping rollup targets | The package's documented extension points; separate definitions | [NP-DPE] |

In a nonprofit org, check A40 first: skills `salesforce-npsp` and
`salesforce-nonprofit-cloud` list the package behaviours a design must not fight.

## Sources

- [WA-T] https://architect.salesforce.com/docs/architect/well-architected/guide/trust.html
- [WA-R] https://architect.salesforce.com/docs/architect/well-architected/guide/reliability.html
- [WA-RC] https://architect.salesforce.com/docs/architect/well-architected/guide/resource-and-cost-optimization.html
- [WA-F] https://architect.salesforce.com/docs/architect/well-architected/guide/fairness.html
- [P-IAM] https://architect.salesforce.com/docs/architect/well-architected/guide/trust-identity-access-patterns
- [P-DATA] https://architect.salesforce.com/docs/architect/well-architected/guide/trust-data-protection-patterns
- [P-COMP] https://architect.salesforce.com/docs/architect/well-architected/guide/trust-compliance-patterns
- [P-SDEV] https://architect.salesforce.com/docs/architect/well-architected/guide/trust-secure-development-patterns
- [P-TAG] https://architect.salesforce.com/docs/architect/well-architected/guide/trust-agentic-patterns
- [P-AVAIL] https://architect.salesforce.com/docs/architect/well-architected/guide/reliability-availability-performance-patterns
- [P-SCALE] https://architect.salesforce.com/docs/architect/well-architected/guide/reliability-scalability-patterns
- [P-DR] https://architect.salesforce.com/docs/architect/well-architected/guide/reliability-disaster-recovery-patterns
- [P-DEVOPS] https://architect.salesforce.com/docs/architect/well-architected/guide/operational-excellence-devops-patterns
- [P-AUTO] https://architect.salesforce.com/docs/architect/well-architected/guide/operational-excellence-automation-patterns
- [P-CODE] https://architect.salesforce.com/docs/architect/well-architected/guide/resource-and-cost-optimization-code-quality-patterns
- [P-STOR] https://architect.salesforce.com/docs/architect/well-architected/guide/resource-and-cost-optimization-storage-api-patterns
- [P-A11Y] https://architect.salesforce.com/docs/architect/well-architected/guide/fairness-accessibility-patterns
- [DG-RT] https://architect.salesforce.com/docs/architect/decision-guides/guide/record-triggered
- [DG-ASYNC] https://architect.salesforce.com/docs/architect/decision-guides/guide/async-processing
- [DG-EDA] https://architect.salesforce.com/docs/architect/decision-guides/guide/event-driven
- [DG-INT] https://architect.salesforce.com/docs/architect/decision-guides/guide/data-integration
- [DG-FORMS] https://architect.salesforce.com/docs/architect/decision-guides/guide/build-forms
- [DG-AGENT] https://architect.salesforce.com/docs/architect/decision-guides/guide/determining-agentic-vs-traditional-workflow-automation
- [F-SHARE] https://architect.salesforce.com/docs/architect/fundamentals/guide/platform-sharing-architecture
- [F-ASYNC] https://architect.salesforce.com/docs/architect/fundamentals/guide/async-fundamentals
- [F-AGINT] https://architect.salesforce.com/docs/architect/fundamentals/guide/agentic-integration-patterns
- [TH-FLOW] https://trailhead.salesforce.com/content/learn/modules/flow-implementation-1/avoid-flow-limits
- [REL-S26] https://www.salesforce.com/blog/summer-26-release-architect-highlights/
- [REL-W27] https://www.salesforce.com/blog/winter-27-release-architect-highlights/
- [SK-APEX] https://github.com/forcedotcom/sf-skills/blob/main/skills/platform-apex-generate/SKILL.md
- [NP-DPE] Community guidance on Nonprofit Cloud rollup definitions: https://github.com/SFDO-Community-Sprints/npc-best-practices/blob/main/docs/Industry%20Common%20Components/DPE%20Best%20Practices.md
