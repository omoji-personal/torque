# Flow build checklist

Work top to bottom; each step ends with its check. State: Winter '27 (API 68.0), checked
2026-11-14. `[check]` = testable in the flow file. **verify** = not confirmed in a fetched
primary source: verify in the org (an element name: in a retrieved flow) or the current
documentation. field-reported = seen in client work, usually one org.

## 1. See what already runs on the object

1. Run `torque advisory impact --target-org ALIAS --sobject Object__c --operation update`
   (also `insert` and `delete` when they apply). Read its "not covered" list.
2. Open Setup > Flow Trigger Explorer: it shows the object's record-triggered flows in run
   order [TH-FTE].
3. Find what that inventory can miss. Processes:
   `SELECT ApiName, ProcessType, TriggerType FROM FlowDefinitionView WHERE IsActive = true`
   (a `ProcessType` that is not a flow type is a process; Process Builder reached end of
   support on 2025-12-31 [WA-RC] and existing processes keep running, **verify**). Scheduled
   work: `SELECT CronJobDetail.Name, State, NextFireTime FROM CronTrigger`. Package triggers:
   `sf package installed list --target-org ALIAS`, then that package's skill or documentation.
4. Count the object's automations. Under 15 suits a record-triggered flow; 15 to 30 a flow
   that calls invocable Apex; over 30, or thousands of records per load, Apex [DG-RT].
5. If Apex triggers are the object's entry point, stop and ask before you add a flow, and the
   reverse: "Use one entry point per Salesforce Object" [DG-RT]. Where a managed package owns
   the trigger, the mix is a given: learn the order instead.
6. Read the existing flows on the same fields. Merge a fix into the flow that owns the logic,
   leaving its other logic untouched, before you build a new flow.

Check: the inventory is saved with the change (field-reported: a scan limited to
record-triggered flows missed a Process Builder process that sent marketing events, and an
existing flow already did the job a new one was designed for).

## 2. Pick the trigger and the timing

| Need | Use | Source |
| --- | --- | --- |
| Set fields on the record being saved | Before-save record-triggered flow | [DG-RT] |
| Create or update other records, call an action, send email | After-save record-triggered flow (invocable actions run only after save) | [DG-RT] |
| Follow-up that can wait until after commit, a simple callout | Run Asynchronously path (shares the daily async allocation) | [DG-RT] [F-ASYNC] |
| Action at a time relative to a date on the record | Scheduled path | [DG-RT] |
| Periodic sweep of many records | Schedule-triggered flow, or Apex for volume | [DG-ASYNC] |
| Guided input over several screens | Screen flow | [DG-FORMS] |
| Logic several flows share | Subflow | [WA-OE] |
| Long sequence with several people and steps | Flow Orchestration | [P-AUTO] |
| Block a save with a message | Validation rule first; the Custom Error element where a flow is needed (not available in subflows) | [DG-RT] |
| Savepoints, partial success, field-level errors, after undelete | Apex | [DG-RT] |

- Prefer one flow that sets the value when the record is created over a second automation
  that corrects it later (field-reported: one before-save flow with three entry conditions
  replaced a four-part design).
- Build nothing new in Workflow Rules or Process Builder. `[check]` no new `rules` in a
  `*.workflow-meta.xml` file and no new process-type flow file.

Check: `[check]` `start.triggerType`; the reason for after-save or for Apex is recorded.

## 3. Entry conditions and order

1. Make the flow run only for the records and the change that matter: "Execute the logic
   only if a specific field of interest has actually changed" [DG-RT]. Compare the new value
   with the prior value, or choose the option that runs the flow only when a record is
   updated to meet the conditions. That option fires on the move into the condition and not
   again while the record stays in it [ADV `entry-condition-requirements`].
2. Where the flow creates records, key on the transition (status changed to Approved), not on
   the value, so a second save does not create a second set.
3. A save with unchanged values still fires every flow whose conditions do not test for a
   change [ADV `no-op-update-is-not-free`].
4. If loads or integrations must skip the flow, test a custom permission in the entry
   conditions (`$Permission`) and grant it by permission set, never by naming a profile or
   user [DG-RT]. Add this only when a load or integration needs it; record who holds it.
5. Set the trigger order. Values run from 1 to 2,000; flows with no value run between 1,000
   and 1,001 by activation date; ties break on API name; order never moves an after-save flow
   ahead of a before-save flow or an Apex trigger (**verify** all four on the help page).
6. Place the flow in the save order before you rely on a value. Before-save flows run before
   Apex before-triggers and validation rules; duplicate rules can still block the save
   afterwards; after-save flows run after triggers and assignment and workflow rules; email,
   asynchronous Apex and asynchronous paths run after commit, so a read straight after the
   save does not show them [ADV `order-of-execution`, `duplicate-rules-block-the-save`,
   `post-commit-work-is-invisible-to-the-caller`].

Check: `[check]` `start.filters` or `start.filterFormula`, and
`start.doesRequireRecordChangedToMeetCriteria` where used (**verify** names); `[check]`
`triggerOrder`. Then rerun one existing path on the object, for example an approval
(field-reported: a new before-save flow changed the run order and submissions skipped it).

## 4. Structure and bulk

- One purpose per flow. One flow holding all of an object's automation is an anti-pattern,
  and splitting into several well-conditioned flows costs little [DG-RT].
- No Get, Create, Update, Delete or action inside a loop. One element is one query or one
  statement however many records it handles; put it after the loop [TH-FLOW].
- A flow shares the limits of the transaction that started it: 100 queries, 150 DML
  statements, 10,000 records written, 10 seconds of CPU [TH-FLOW]. Records arrive up to 200
  at a time; 200 should cost the same number of queries and statements as one [WA-R].
- Winter '27 adds collection filtering without a loop and retry on record locks [REL-W27]:
  check whether the loop is still needed.
- Decide what happens when a Get finds nothing. Reuse through subflows, and never let two
  flows call each other (analyzer `CyclicSubflow`).
- Give compute-heavy steps to invocable Apex and keep entry conditions and orchestration in
  the flow [DG-RT]. An invocable method takes and returns lists and carries a description.
- Do not chain your own flows with platform events: "Don't use events to orchestrate
  automation; use subflows or Flow Orchestrator" [DG-EDA].

Check: `[check]` no data element or `actionCalls` on a loop's for-each path (analyzer
`DbInLoop`); a 200-record test passes (step 9).

## 5. Fault paths

- Add a fault connector to every `recordLookups`, `recordCreates`, `recordUpdates`,
  `recordDeletes` and `actionCalls` element. For a subflow see `gotchas.md`, item 3.
- Make the path do its three jobs: a plain message for the user; enough for an admin to act
  (flow, element, record, user, error text) at a monitored address or in a log record; no
  half-done work.
- Do not add a catch-all handler that hides the cause, and do not widen access or relax a
  validation rule to make a fault go away. Fix the cause.
- Find where fault email goes today (Setup > Process Automation Settings) and read past
  failures (Setup > Paused and Failed Flow Interviews). **verify** both page names in Setup.

Check: `[check]` `faultConnector` on each data element and action (analyzer
`MissingFaultHandler`, selector `flow`). Then force the fault in a sandbox: a plain user
message, one log row or notice, no partial records.

## 6. Nothing hard-coded

| Value | Put it in | Why |
| --- | --- | --- |
| A record, queue, group or record type | A lookup by DeveloperName or Name at run time | Ids differ between a sandbox and production |
| A threshold, mapping or switch the admin may change | A custom metadata record with a description [DG-RT] | Changed in Setup, moves with deployments |
| Text a user reads | A custom label | Changed without editing the flow |
| Who may use or skip the logic | A custom permission in a permission set | Assigned and removed per person |
| An endpoint or secret | A named credential | Never in a flow, a label or custom metadata |

Check: `[check]` no 15 or 18 character Id literal, no `my.salesforce.com` host, no user or
profile name in values, filters or formulas (analyzer `HardcodedId`). field-reported: a user
name in a run-as setting survived a sandbox refresh and failed there.

## 7. Running context and access

- Defaults: record-triggered, scheduled and platform-event flows run in system context
  without sharing; a screen flow run by a user runs as that user; an autolaunched flow takes
  its caller's context (**verify**).
- Change the context only for a stated requirement. Keep privileged steps small: run the main
  flow as the user and put the privileged step in a subflow (**verify**).
- The people who run the flow need permission to run it and access to what it reads and
  writes in user context. Grant both by permission set and test as one of them.
- Do not run system-context flows for guest users on a public site: a system-context Get with
  unrestricted fields can expose data in the browser [DG-FORMS].

Check: `[check]` `runInMode`; the reason is in the description when it is
`SystemModeWithoutSharing` [LFS]. The tag can be absent when the default applies (**verify**).

## 8. Describe and name

- Flow description: the problem it solves, the trigger and entry conditions, the outcome,
  where it is launched, and the story or ticket. Decisions, loops, assignments, data elements
  and actions each say what they do and why; each variable says what it holds. For an action
  or a subflow the description is where the logic is explained [DG-RT].
- Good: "Before save on Case, when Priority changes to High: sets Escalation Due to two
  business days ahead. Story SUP-112." Bad: "Case flow v3". Do not restate the name, and
  never write a version number (field-reported: one said version 17 while 22 was live).
- Name by the org's convention; where none exists, a pattern such as `Object_FlowType_Action`
  works (**verify**). Short API names; elements as verb phrases, decisions as questions.
- If a script generates the flow, keep the descriptions as data and re-apply them on every
  regeneration, or the next build removes them (field-reported).

Check: `[check]` `description` on the flow and on every element and resource (analyzer
`MissingDescription`, selector `flow`); after deploy, `Description` on `FlowDefinitionView`
(field-reported). Examples: `salesforce-build/references/documentation-and-naming.md`.

## 9. Test

| Path | What to run | Pass means |
| --- | --- | --- |
| Each outcome | One named scenario per decision outcome | The expected branch ran and the data is right |
| Negative | Records that must not enter; blank, non-numeric and cleared inputs | Nothing changed, or the right message showed |
| Bulk | 200 records in one transaction | Same result per record, no limit error |
| Repeat | Save the same record again | No duplicate records, no second action |
| Fault | Force a failure on a data element | Plain user message, one log row, no partial save |
| As the user | Run as a person with the intended permission sets | Works without admin rights |
| Neighbours | One path that already worked on the object | Still works |

- Run flow tests with `sf logic run test --test-category Flow` (see `--help`) [PLUGIN-APEX].
  They do not cover delete-triggered flows or asynchronous paths and do not count toward
  Apex coverage (**verify**): pair them with an Apex test that saves 200 records.
- Debug as another user: sandboxes only, after "Let admins debug flows as other users" is
  on in Process Automation Settings (**verify**). Then clean up: `gotchas.md`, item 6.
- Verify a scheduled flow by its real scheduled run, and log any schedule you moved for a
  test as an open restore (field-reported: one stayed on the test time for days).
- Check: the result names the org, the user, the flow version and what was not tested.

## 10. Leftovers and the record

- Remove unused resources and unreachable elements (analyzers `UnusedResource`,
  `UnreachableElement`), or list them with anything kept only for an old flow version.
- Record the flow API name, source file, org, deploy job id, tests run and what is not yet in
  production (`salesforce-delivery/references/environments-and-release.md`).

## Sources

- [DG-RT] https://architect.salesforce.com/docs/architect/decision-guides/guide/record-triggered
- [DG-ASYNC] https://architect.salesforce.com/docs/architect/decision-guides/guide/async-processing
- [DG-EDA] https://architect.salesforce.com/docs/architect/decision-guides/guide/event-driven
- [DG-FORMS] https://architect.salesforce.com/docs/architect/decision-guides/guide/build-forms
- [F-ASYNC] https://architect.salesforce.com/docs/architect/fundamentals/guide/async-fundamentals
- [TH-FLOW] https://trailhead.salesforce.com/content/learn/modules/flow-implementation-1/avoid-flow-limits
- [TH-FTE] https://trailhead.salesforce.com/content/learn/modules/record-triggered-flows/meet-flow-trigger-explorer
- [WA-OE] https://architect.salesforce.com/docs/architect/well-architected/guide/operational-excellence.html
- [WA-R] https://architect.salesforce.com/docs/architect/well-architected/guide/reliability.html
- [WA-RC] https://architect.salesforce.com/docs/architect/well-architected/guide/resource-and-cost-optimization.html
- [P-AUTO] https://architect.salesforce.com/docs/architect/well-architected/guide/operational-excellence-automation-patterns
- [REL-W27] https://www.salesforce.com/blog/winter-27-release-architect-highlights/
- [PLUGIN-APEX] https://raw.githubusercontent.com/salesforcecli/plugin-apex/main/README.md
- [LFS] Lightning Flow Scanner (community): https://github.com/Flow-Scanner/lightning-flow-scanner
- [ADV] Torque advisory catalogue (`torque advisory notes --command TEXT`); entry ids inline.
- **verify** items come from pages read only as search extracts: flow best practices
  (https://help.salesforce.com/s/articleView?id=sf.flow_prep_bestpractices.htm&type=5), trigger order
  (https://help.salesforce.com/s/articleView?id=sf.flow_task_trigger_run_order.htm&type=5), flow tests
  (https://help.salesforce.com/s/articleView?id=sf.flow_concepts_testing.htm&type=5), running context
  (https://developer.salesforce.com/docs/platform/secure-coding/guide/secure-coding-considerations-flow-design.html),
  naming (https://admin.salesforce.com/blog/2025/planning-for-flow-success-building-automation-that-scales).
- field-reported: lessons from real client work with Torque and its predecessor (one
  consultant, about a dozen clients, mostly sandboxes): working notes, an after-action review
  of 166 rework and defect episodes, and a classification of 273 failed Salesforce CLI calls.
  Not re-tested for this release.
