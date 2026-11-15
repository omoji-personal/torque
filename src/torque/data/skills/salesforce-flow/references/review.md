# Reviewing a flow

Use this to explain or review a flow someone else built, or your own before it ships. A
review is read-only: retrieve and query. Do not edit, activate, or run a debug that can
commit unless that was asked for. State described: Winter '27 (API 68.0), checked
2026-11-14. **verify** and field-reported are used as in `gotchas.md`.

## 1. Gather the evidence

1. Find which version is active: `torque advisory flow --target-org ALIAS --api-name NAME`.
   A local file or a fresh retrieve is the latest version, which may not be the one that runs.
2. Get the definition of the version you are reviewing. Say which version it is.
3. List the other automation on the object (`build-checklist.md`, step 1). Look for overlap:
   another flow, trigger, process or package handler that writes the same fields.
4. Find where it is launched (record save, schedule, button, page, another flow, Apex) and
   who runs it.
5. Run the checks on the file: `torque check metadata PATH_TO_FLOW` runs Torque's flow rules
   and Code Analyzer with the flow rules included. Without Torque: skill
   `salesforce-code-analyzer-review`.
   If the community Lightning Flow Scanner is already installed, `sf flow:scan` adds checks
   such as trigger order, null handling and API version [LFS]. Do not install tools only for
   a review.
6. If runtime behaviour matters, read recent failures (Setup > Paused and Failed Flow
   Interviews, fault email) before forming a view (**verify** the page name).

## 2. Explain it before you judge it

Write the explanation first. It is the first deliverable and it exposes misreadings.

- Purpose in one sentence, in the business's words.
- Trigger and timing: object, create or update or delete, before or after save, schedule.
- Entry conditions, in plain words.
- Each path: the decision, what it reads, what it writes, what it sends outside the org.
- Screens in order, for a screen flow.
- Subflows and Apex actions it calls, and what each returns.

Tie each statement to an element API name. Mark what you read in the source and what you
observed at run time. For a client or a trainee, describe each step by what the user sees and
what it does; for a builder, give exact elements and a diff.

## 3. Review checklist

| Area | Look for | Where |
| --- | --- | --- |
| Right tool | A formula, validation rule or before-save update that would do the same job. Raise it only when it is functionally equivalent | `start`, the elements that write |
| Timing | After-save flow that only updates its own record | `start.triggerType`, analyzer `SameRecordUpdate` |
| Entry | No conditions; conditions on the value where the transition is meant; runs on every save | `start.filters`, `start.filterFormula`, analyzer `TriggerEntryCriteria` |
| Order | No trigger order where several flows share the object | `triggerOrder`, Flow Trigger Explorer |
| Bulk | Data element or action on a loop's for-each path; a subflow called per record that queries | `loops`, analyzer `DbInLoop` |
| Recursion | A flow that updates the record that started it after save; two flows that update each other's objects | The update elements and the other flows' entry conditions |
| Faults | Data element or action with no fault path; a path that only swallows the error; partial save on a collection | `faultConnector`, analyzer `MissingFaultHandler` |
| Transaction | Callout after a database write in the same transaction; screens and waits that split the work | `actionCalls` order, analyzer `TriggerCallout` |
| Nulls | A Get whose empty result is used; blank inputs; empty collections | The element after each `recordLookups` |
| Context and access | System context without sharing with no reason; who may run it; guest users | `runInMode`, the flow's access settings |
| Hard-coded values | Ids, host names, user or profile names, thresholds in formulas | Analyzer `HardcodedId`, a search for 15 or 18 character Ids |
| Documentation | Missing or stale description on the flow, elements and resources; a version number in a description; default or copied names | `description`, analyzers `MissingDescription`, `DefaultCopy` |
| Leftovers | Unused resources, unreachable elements | Analyzers `UnusedResource`, `UnreachableElement` |
| Version | API version lowered or far behind; a draft newer than the active version | `apiVersion`, `FlowDefinition` |
| Side effects | Email, callouts, records in other objects, platform events: what a test or a data load would send | The action and create elements |
| Tests | No flow test or Apex test covers the paths; nothing proves 200 records | The project's tests |
| Legacy | Process Builder or workflow rules on the same object | `build-checklist.md`, step 1 |

Apply the rows the change needs. Do not ask for a bypass in every flow: ask for one only
where a load or an integration has to skip the logic. The analyzer rule names are those of
Salesforce Code Analyzer v5 (list them with `sf code-analyzer rules --rule-selector flow
--view detail`; each rule's description says what it tests). A row is a finding when its
effect is real: before you call a design defective, show the functional equivalent, the
order of execution it needs, or what a failure there actually leaves behind.

## 4. Write the findings

| Field | Content |
| --- | --- |
| Severity | Blocks release (data loss, wrong data, exposure, breaks at volume) / Fix before production / Improve when next touched |
| Where | Flow API name, version reviewed, element API name |
| Finding | What is wrong, in one sentence |
| Evidence | Read in source, or observed (org, user, date, what was run) |
| Impact | Who or what is affected, and when |
| Fix | The smallest change that removes it |

- Treat untested behaviour as a gap, not a defect.
- Report one defect once, under the row that fits best.
- Read each finding again against the file before you send the list (field-reported: one
  re-check dropped two findings, changed three and corrected a count).
- State what you checked, what you did not, and which version and org the review covers.

## 5. When the flow is a colleague's

- Give the evidence and the fix. Do not rule on their diagnosis or their design choices
  without a sourced reason (field-reported).
- Do not edit their flow. Send findings; if a fix is yours to make, agree it first.
- If the version history shows them editing now, stay read-only until they agree
  (field-reported, a shared sandbox).
- Sort findings by who must act: yours, the package's, the client's.

## 6. If a fix is requested

Follow `activation-and-deploy.md`, section 5: retrieve the current definition, edit only the
elements the fix needs, validate, test the affected paths and one neighbouring path, deploy
within the scope you were given, read back the active version. Do not replace the whole flow.

## 7. Save

Keep the explanation, the findings, the tests run and the paths left unresolved with the
client's record, each with the org, the flow version and the date.

## Sources

- Salesforce Code Analyzer flow rules (read as a search extract): https://developer.salesforce.com/docs/platform/salesforce-code-analyzer/guide/rules-flow.html
- [LFS] Lightning Flow Scanner (community, MIT): https://github.com/Flow-Scanner/lightning-flow-scanner
- Record-triggered automation decision guide: https://architect.salesforce.com/docs/architect/decision-guides/guide/record-triggered
- Well-Architected automation patterns: https://architect.salesforce.com/docs/architect/well-architected/guide/operational-excellence-automation-patterns
- field-reported: lessons from real client work with Torque and its predecessor (one
  consultant, about a dozen clients, mostly sandboxes): working notes, an after-action review
  of 166 rework and defect episodes, and a classification of 273 failed Salesforce CLI calls.
  Not re-tested for this release.
