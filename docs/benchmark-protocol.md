# Comparative acceptance protocol

Protocol prepared 2026-09-07. **No comparative trials or outside-user results have
been collected.** Targets are decision criteria, not product claims. Use this
protocol before claiming that Torque is faster, easier or industry-leading.

## Questions and baselines

1. Can an unfamiliar consultant install the artifact and reach a useful sample
   handoff without author coaching?
2. Can a fresh assistant/session recover the selected client's objective,
   decisions, current failures, exact target and next action?
3. Does Torque reduce resumption/handoff effort or acceptance omissions compared
   with the same assistant and Salesforce tools without Torque?

The primary baseline uses the same model, reasoning settings and official
Salesforce CLI/skills/plugin tools. Give it a normal project README and the same
business inputs, source and starting org. Record the exact tool versions and
setup. A second comparison can use an existing CCI or sfdx-hardis project. Include
commercial products only with access to the relevant tier/features; public
marketing pages are not performance evidence.

## Synthetic tasks

Use distinct but equivalent task pairs. Never import real client records.

- Contact preference: save a restricted picklist as the intended user, reopen it,
  and test an unassigned user's denied edit.
- Urgent follow-up: on a service request becoming urgent, create exactly one task
  for its owner; repeated unrelated edits must not create duplicates.
- Scoped data correction: update one field, observe a subsequent unrelated edit,
  restore the original field and preserve the later edit.
- Handoff continuation: stop after a failed check or partial job; a fresh session
  must identify the actual state and complete the remaining work.

Before each task, establish the expected positive, negative, ownership/access,
repeated-edit and cleanup results. Record exact initial artifacts and disposable
org identity. A deployment report, admin query or illustrative screenshot cannot
substitute for intended-user acceptance. Retain failed observations after fixes.

## Trial method

Run at least 10 paired equivalent trials before describing a measured effect.
Counterbalance Torque/baseline order and task pairing. Separate one-time setup
from active work, waiting for platform jobs, resumption and handoff time. Use the
same person for paired trials where practical and disclose prior familiarity.
Do not reuse a solution learned in the first trial without accounting for it.

For continuity, include two private clients with two changes each and distinct
sentinel facts. Switch clients and assistant hosts. Start each resumed host with
only the stated continuation inputs; record any extra author intervention.
Correct target selection and omission of other-client sentinels are required for
the application contract, not evidence of OS-level process isolation.

For outside usability, recruit at least three new users for onboarding and five
Salesforce practitioners for handoff/follow-up. Do not coach through a failed step
without recording the intervention. Suggested targets: median sample handoff under
10 minutes; at least four of five practitioners continue without author coaching.
These are small exploratory samples, not population-wide estimates.

## Measurements

| Field | Definition |
| --- | --- |
| Setup minutes | Prerequisites, installation and initial configuration, separate from task work |
| Active minutes | Working time excluding unattended platform/model waits, measured consistently |
| Resume minutes | Fresh-session start to a correct, executable next action |
| Handoff minutes | Active preparation time to a handoff meeting the fixed answer key |
| Repeated context questions | Questions whose answers were already supplied in continuation inputs |
| Corrective edits | Changes needed to fix a wrong implementation or erroneous recovered state |
| Acceptance omissions | Expected cases skipped, mislabeled or incorrectly treated as passed |
| Handoff score | Correct objective, current state, target, changed artifacts, unresolved checks and next step, each 0/1 |
| Isolation/identity failures | Wrong client/org/user used or other-client sentinel disclosed |
| Intervention and failures | Coaching, unavailable dependencies, auth problems, timeouts and abandoned trials |

Save one row per trial to `results.csv` with trial ID, condition, paired task ID,
participant pseudonym, host/model/tool versions, setup/active/wait/resume/handoff minutes,
context questions, corrective edits, omissions, handoff score, identity failures,
intervention count, terminal outcome and nonprivate evidence reference. Keep the
prewritten answer key and capture timing separately from assistant self-report.

Report medians and paired differences with the individual measurements, missing
trials and failure reasons. The primary endpoint is the sum of resume and handoff active minutes per trial,
compared within each task pair; report each component separately as secondary
measurements. A proposed useful effect is at least 20% lower median paired
relative combined effort without increased omissions. This is a target, not a result.
Do not delete inconvenient failures, change the primary endpoint after observing
results, or generalize a small author-run trial into a claim about all consultants.

A leadership claim needs a defined category, current competitors, reproducible
method, independent use and sustained evidence. Neither command count nor passing
unit tests establishes that claim.
