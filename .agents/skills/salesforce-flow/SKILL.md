---
name: salesforce-flow
description: Build, change, review or debug any Salesforce Flow, including record-triggered, scheduled, screen and autolaunched flows and subflows. Use it before opening or writing a flow file (.flow-meta.xml), before adding automation to an object, when a flow error email or a failed interview needs a cause, when reviewing someone else's flow, and before telling anyone when or whether a flow runs. Covers before-save or after-save, entry conditions and trigger order, what else fires on the object, loops, fault paths, hard-coded Ids, running context, descriptions, screen flows, testing, activation and versions on deploy, and field-tested gotchas with a check each.
---

# Salesforce Flow

Read this before you open, write, review or explain a flow. State described: Winter '27
(API 68.0), checked 2026-11-14. Flow behaviour changes with each release and with the flow's
own API version, so where a line says "Check", run the check in the target org.

Marks used in this skill:

- `[check: flow.no-fault-path]` and the like: the `torque check metadata` rule that tests the
  statement in the flow file. "Analyzer" names a Code Analyzer rule; "by hand" says what to read.
- **verify**: not confirmed in a fetched primary source. Verify in the org or the current
  documentation before you rely on it or repeat it to a client.
- field-reported: seen in real client work, usually in one org. Run its check first.

## Start here

| You are about to | Do this first | Then read |
| --- | --- | --- |
| Build a new flow | List what already runs on the object (rule 2); pick the simplest tool (rule 1) | `references/build-checklist.md` |
| Change an existing flow | Read the active version, retrieve the flow from the target org, keep the baseline (rule 14) | `references/activation-and-deploy.md` |
| Deploy or activate a flow | Find out whether the target activates on deploy; plan the read-back (rule 15) | `references/activation-and-deploy.md` |
| Review a flow | Get the active version and the object's other automation | `references/review.md` |
| Debug a fault, or say when a flow runs | Read the active version, its schedule, entry conditions and run-as user | `references/gotchas.md` |

Two read-only commands answer most first questions:

```sh
torque advisory flow --target-org ALIAS --api-name Flow_Api_Name
torque advisory impact --target-org ALIAS --sobject Object__c
```

The first reports whether the flow has an active version, read from `FlowDefinition` and
`FlowDefinitionView`. The second lists active Apex triggers, record-triggered flows,
validation rules and workflow rules on the object and prints what it does not cover. Process
Builder processes, scheduled flows and managed-package automation can be missing from it:
look for those yourself (`references/build-checklist.md`, step 1).

## The standard for every flow

| # | Rule | How to check it |
| --- | --- | --- |
| 1 | Use a flow only when a simpler tool does not fit: a formula field, a validation rule, a roll-up summary. Say why when you step up to a flow, and again when you step past it to Apex. | Judgement: the reason is in the flow description or the change record. Decision table: `salesforce-build/references/choosing-the-tool.md`. |
| 2 | List everything that fires on the object before you add to it, and use the entry mechanism the object already uses [DG-RT]. | The inventory is saved with the change: `torque advisory impact`, Setup > Flow Trigger Explorer, Process Builder, scheduled flows, package triggers. |
| 3 | Change the triggering record's own fields before save. Use after save only for other records, actions, email and callouts [DG-RT]. | `start.triggerType` [LFS]. `[check: flow.after-save-same-record]` an after-save flow whose only write is to the triggering record: move it before save unless the order of execution needs after save. Analyzer: `SameRecordUpdate`. |
| 4 | Give every record-triggered flow entry conditions. An update flow runs only when the field that matters changed [DG-RT]. | `[check: flow.no-entry-conditions, flow.reruns-on-every-update]`. Analyzer: `TriggerEntryCriteria`. |
| 5 | Set the trigger order on every record-triggered flow you add, then rerun the paths that already worked on the object. | By hand: `triggerOrder` in the file [LFS]; Flow Trigger Explorer shows the order. |
| 6 | Put no Get, Create, Update, Delete or action inside a loop. Collect in the loop, write once after it [TH-FLOW]. | `[check: flow.work-in-loop]`. Analyzer: `DbInLoop`. |
| 7 | Add a fault path to every data element, action and subflow. It must do the three jobs below. A before-save flow runs no actions [DG-RT]; whether its Get Records offers a fault path, read in Flow Builder. | `[check: flow.no-fault-path]` `faultConnector` on each [SAMPLE-FLOW]; before-save flows are skipped. Analyzer: `MissingFaultHandler`. |
| 8 | Hard-code no record Id, org URL, user name or profile name. Look up by DeveloperName, or read custom metadata, a custom label or an input variable. | `[check: flow.hard-coded-id]` Ids in values, filters, formulas and templates. Analyzer: `HardcodedId` (Ids only). By hand: search the file for host names and user names. |
| 9 | Leave the running context at its default unless a requirement says otherwise. A flow that runs in system context without sharing says why in its description. | `[check: flow.system-mode-without-sharing]` `runInMode` [LFS]. |
| 10 | Describe the flow and every element and resource. Flow description: purpose, trigger and entry conditions, outcome, where it is launched, story or ticket. Never a version number. | `[check: doc.description-missing, doc.flow-elements-undescribed, doc.description-names-version]`. Analyzer: `MissingDescription`. |
| 11 | Follow the org's naming convention. Where none exists, propose one and use it everywhere. No default or "Copy of" names. | `[check: flow.default-names]`. Analyzer: `DefaultCopy`. By hand: the file name against the agreed pattern. |
| 12 | New flows carry the current API version. Do not lower a flow's version; treat raising it as a change to test. | `[check: flow.api-version-behind]` `apiVersion` against the project's and the baseline's. |
| 13 | Test every path: each outcome, bad input, 200 records, a repeat save, the fault path, and as the intended user. | Test results name the user, the org, the flow version and what was not tested. |
| 14 | Change an existing flow without touching its other logic: retrieve it from the target org first, edit only your elements, never regenerate the whole flow. | The diff against the retrieved baseline shows only your change. |
| 15 | Deploy deliberately. Afterwards read which version is active and say so. | `torque advisory flow` after the deploy; the active version is the intended one. |

Run the checks on the change with `torque check metadata PATH --changed-from dry-run.json`:
it runs Code Analyzer with `--rule-selector Recommended --rule-selector flow` and merges the
findings. `MissingFaultHandler` and `MissingDescription` are not tagged Recommended, so a
default analyzer run skips them. Without Torque, and for reading results: skill
`salesforce-code-analyzer-review`.

## What a fault path must do

1. Tell the user what failed and what to do next, in plain words, with no internal error
   text [WA-R].
2. Tell an admin enough to act: flow, element, record, user and the error text, sent to a
   monitored recipient or written to a log record [P-OBS]. Without this the error email goes
   to whoever last modified the flow, unless Process Automation Settings says otherwise
   (**verify**).
3. Leave no half-done work. A handled fault on a collection Create kept the rows that had
   succeeded (observed in a developer org). Flow has no savepoint or partial-DML control
   [DG-RT]: where all-or-nothing matters, roll back where the flow type offers it or move the
   transaction to Apex.

A fault path does not rescue a governor-limit failure (**verify**). Details and the subflow
case: `references/gotchas.md`.

## Screen flows

- Ask only for data the org does not already hold [P-AUTO].
- A screen ends the transaction [TH-FLOW]. After a database change, decide whether the user
  may go back, or the change is made twice (**verify**).
- A screen flow on a record page can need more access than the profile's flow access, and a
  formula that compares a choice field to text sees the label, not the value (both
  field-reported, with checks in `references/gotchas.md`).
- Do not run a system-context flow for guest users on a public site [DG-FORMS].
- Test it in a browser as the user. Sign the browser in with `torque browser login`
  (see `torque browser login --help`); do not type a password or paste a sign-in address.

## Activation and versions

- A retrieve returns the latest version, not the active one [ADV]. Query the org before you
  describe or edit a flow.
- "Deployed" and "running" are different states. Production can deploy an active flow as
  inactive, depending on a Process Automation setting (**verify**).
- After every flow deploy, read the active version and report it with the org and the date.
- Never put a version number in a description: it goes stale with the next version
  (field-reported).

Commands and the full procedure: `references/activation-and-deploy.md`.

## References

- `references/build-checklist.md`: the build steps in order, with the metadata to inspect.
- `references/activation-and-deploy.md`: versions, activation, deploying and changing a flow
  in an org other people use.
- `references/gotchas.md`: platform and metadata traps, each with a check.
- `references/review.md`: how to review someone else's flow and what to report.

A path that starts with another skill's name, such as
`salesforce-build/references/choosing-the-tool.md`, is relative to the skills folder that
holds this skill.

## Sources

- [DG-RT] Record-triggered automation decision guide: https://architect.salesforce.com/docs/architect/decision-guides/guide/record-triggered
- [DG-FORMS] Building forms decision guide: https://architect.salesforce.com/docs/architect/decision-guides/guide/build-forms
- [TH-FLOW] Trailhead, avoid flow limits: https://trailhead.salesforce.com/content/learn/modules/flow-implementation-1/avoid-flow-limits
- [WA-R] Well-Architected, Reliability: https://architect.salesforce.com/docs/architect/well-architected/guide/reliability.html
- [P-OBS] Well-Architected observability patterns: https://architect.salesforce.com/docs/architect/well-architected/guide/operational-excellence-observability-patterns
- [P-AUTO] Well-Architected automation patterns: https://architect.salesforce.com/docs/architect/well-architected/guide/operational-excellence-automation-patterns
- [SAMPLE-FLOW] Salesforce sample flow with descriptions and fault connectors: https://github.com/trailheadapps/dreamhouse-lwc/blob/main/force-app/main/default/flows/Create_property.flow-meta.xml
- [LFS] Lightning Flow Scanner source (community), element names: https://github.com/Flow-Scanner/lightning-flow-scanner
- [ADV] Torque advisory catalogue, entries `flow-retrieve-latest-only` and `flow-activation-on-deploy`: `torque advisory notes --command "sf project deploy start --metadata Flow:Name --target-org ALIAS"`
- field-reported: lessons from real client work with Torque and its predecessor (one
  consultant, about a dozen clients, mostly sandboxes): working notes, an after-action review
  of 166 rework and defect episodes, and a classification of 273 failed Salesforce CLI calls.
  Not re-tested for this release.
