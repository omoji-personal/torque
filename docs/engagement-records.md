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

The first line is POSIX shell. In Windows PowerShell, capture the ID this way; the other
lines then run as written:

```powershell
$change_id = (torque change create --workspace ../torque-private --client sample --title "Record contact preference" --outcome "Service staff can find and use the requester's preferred contact method" --criterion "An intended staff user can save Phone or Email" --criterion "The value remains after reopening the record" --criterion "A user without the intended access cannot edit the field" --org sample-sandbox --json | ConvertFrom-Json).id
```

`change create` records a planned org without contacting it. There is no lockstep
state machine, approval token, automatic deployment or forced task completion.
The stable ID selects one change inside one client. `change list` discovers IDs.

## Clients and initiatives

Records work the same for a client and for an internal initiative (work that is not
for a client, such as adopting a tool). Create one with
`torque initiative add NAME --workspace PATH`, then pass `--initiative SLUG` in place
of `--client` to `context`, `session`, `handoff` and every `change` command.
Initiatives never get client-only powers: `--org` on `change create`,
`change verify-deploy`, consent, approvals and connected mode are refused for them.
An archived initiative refuses new sessions, changes, notes and checks until
`torque initiative set-state NAME active --workspace PATH` reopens it.
`torque engagement list --workspace PATH` shows clients and initiatives together.

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

Session evidence is a reference to the original file, while `change check
--evidence` captures a private copy. Session resumption and handoffs recheck the
recorded hash and report changed, missing or unavailable files. An unchanged hash
establishes byte consistency only; the session's result remains user-reported.
These checks leave the original journal untouched. `torque doctor --workspace
PATH --client NAME` inspects all of that client's sessions and change records,
including older entries outside the recent context summary.

A reference to a file inside the workspace is also recorded relative to the workspace,
so it still resolves after the workspace moves or is copied to another machine, Windows
to macOS or Linux included, and handoffs show that relative path rather than the
author's folders. A file outside the workspace is recorded as an external reference
(`"scope": "external"`) and read only at its absolute path: if it is gone, it is missing,
even when a copy exists inside the workspace. A reference from an earlier version, which
could not say which it was, is looked for in the client's own folder when its absolute
path no longer opens.

A record that cannot be read, such as a sync service's conflict copy (`NAME (1).json`),
a stray file or a damaged record (a field missing or of the wrong type included), does
not stop `context`, `handoff`, `session list` or `change show`: they skip it and name it
at the top, so the history shown is visibly incomplete. Doctor reports the client not
ready until the file is moved out or deleted; Torque never rewrites a record. A record
that names another client or initiative, a symlink, evidence inside another engagement,
and an evidence path that leads out of the workspace or the change's evidence folder
(`..`, an absolute path) still stop the read. Approval and recovery code reads change
history strictly, and refuses a damaged record as an error. A workspace, client,
initiative or change that does not exist is named as missing, with the command that
lists or creates it.

Local JSON files live under `clients/CLIENT/changes/CHANGE_ID/` (or
`initiatives/SLUG/changes/CHANGE_ID/`). Each event is
published atomically as a separate private file, preserving concurrent writers.
This is local continuity, not a multi-user server or distributed transaction
system. Symlink escapes and evidence references across clients or initiatives are
refused. Initiative lifecycle updates serialize the complete read, history append and
replacement, so concurrent updates retain both entries.

## Maintenance

Creating `.torque/maintenance` refuses new core record/configuration writes and new
package commands dispatched through `torque`, except help. Direct revert wrappers check
the flag before their approval and dry-run shortcuts, in full, build-only and connected
workspaces. Removing the flag resumes admission. Core reads, context, handoffs and the
workflow updater remain available; package dispatch is conservatively paused even for
commands that may only read.

The flag is an admission check, not cancellation or a lock on every package storage API.
Other direct package entry points and arbitrary file writes are outside this coverage.
Operations already past a check may finish and write package snapshots, results and
cleanup outcomes. Wait for them to finish before changing workspace files. A connected
post-tool change-record append can be refused while maintenance is active; retain its
reported error and package outcomes, then reconcile the change record after maintenance.
Do not treat a missing execution record as proof that no org operation occurred.
