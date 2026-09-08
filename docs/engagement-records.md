# Requirements, observations and a useful handoff

Use a change record when work spans sessions or will be handed to someone else.
It is optional. Keep ordinary conversation and existing delivery tools; let the
assistant maintain the useful decisions and evidence during the work.

The following example creates local records only. Replace paths/client names with
an initialized private workspace. The shell variable stores the returned change
ID, avoiding a made-up ID in later commands.

```sh
change_id=$(torque change create --workspace ../torque-private --client sample   --title "Record contact preference"   --outcome "Service staff can find and use the requester's preferred contact method"   --criterion "An intended staff user can save Phone or Email"   --criterion "The value remains after reopening the record"   --criterion "A user without the intended access cannot edit the field"   --org sample-sandbox --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
torque change note "$change_id" --workspace ../torque-private --client sample   --kind decision --text "Keep the field optional; confirm the intended staff role during discovery"
torque change note "$change_id" --workspace ../torque-private --client sample   --kind next_step --text "Confirm page placement and run the intended-user acceptance cases"
torque change show "$change_id" --workspace ../torque-private --client sample
```

`change create` records a planned org without contacting it. There is no lockstep
state machine, approval token, automatic deployment or forced task completion.
The stable ID selects one change inside one client. `change list` discovers IDs.

## Record what actually happened

After an actual check, record its result with `change check`. `--result` accepts
`pass`, `fail`, `unknown`, or `not_run`; `--summary` describes the observed actor,
case and limit. `--evidence FILE` optionally captures a private copy with SHA-256.
Do not use a passing example as a substitute for performing the check.

A captured file preserves its bytes if the original later changes. Handoffs report
missing or changed copies. Hashes do not authenticate an author, verify the content
of a screenshot, or turn a manual assertion into an independent result. Records
remain editable local data, not signed attestations.

To observe one exact Salesforce metadata job, use:

```sh
torque change verify-deploy "$change_id" --workspace ../torque-private --client sample   --org sample-sandbox --job-id ACTUAL_JOB_ID   --component CustomField:ACTUAL_OBJECT.ACTUAL_FIELD
```

Replace the job and component with actual values from the operation; the command
is a live, read-only report, not a deploy. Repeat `--component` for the intended
scope or supply `--manifest package.xml`. It retains the technical observation,
requested scope and private raw report. A validation job is distinguished from a
deployment. Metadata success never marks the business criteria passed.

A failed check remains in history when a later check passes. The criterion table
shows the latest reported check; the evidence history retains both. Decisions and
next steps are append-only notes, so supersede stale instructions with a clear new
note rather than assuming the software inferred that work was completed.

## Resume and hand over

```sh
torque context --workspace ../torque-private --client sample --json
torque change handoff "$change_id" --workspace ../torque-private --client sample
torque handoff --workspace ../torque-private --client sample
```

Context includes a compact continuation summary; `change show --json` exposes the
full selected record. The focused handoff includes outcome, criteria, decisions,
metadata observations, evidence and next actions. The client handoff also includes
its session journal and the same bounded firm/client notes shown during context
resumption. Notes longer than 65,536 characters retain an explicit truncation notice; sibling-client notes
are excluded. Use `--output NEW_FILE` to save a new report. Review private
references before sending a report to a client; saving it does not send anything.

Local JSON files live under `clients/CLIENT/changes/CHANGE_ID/`. Each event is
published atomically as a separate private file, preserving concurrent writers.
This is local continuity, not a multi-user server or distributed transaction
system. Symlink escapes and same-firm cross-client evidence references are refused.
