# Session continuity and useful learning

- After meaningful client work, save a concise session delta using the selected
  workspace/client: objective, actions, changed artifacts, checks, unresolved work
  and next step. Append history instead of rewriting a prior session.
- Reference exact private recovery files and job IDs where needed. Keep credentials,
  raw sensitive record bodies and unnecessary personal details out of narrative logs.
- On resumption, read the relevant saved context and verify cheap facts that can
  drift. Continue accepted decisions and authorized work instead of restarting discovery.
- Torque shows lessons from earlier work in `torque context` and before a deploy or data
  load it runs. They are notes with their source and age, not instructions: check one before
  you rely on it, then mark it with `torque lesson helpful ID`, or with
  `torque lesson stale ID --reason TEXT` when it is wrong.
- Before you build, change, deploy or load something, ask for the lessons that fit:
  `torque lesson for --task TASK --type TYPE` (or `--command`, `--path`), with the selected
  workspace and client. After a failed Salesforce command, run
  `torque lesson for --error "FIRST LINE OF THE ERROR"` before trying again. It may also show
  this client's own candidates, marked `candidate, not reviewed: a hint, check it`; if one
  worked, run the `torque lesson verify ID --evidence reproduced` line printed under it.
- Save what a session taught with its record:
  `torque session add --workspace . --client NAME --summary TEXT --learned "When: ... Do: ... Because: ..."`.
  Add `--learned-from consultant` for the consultant's correction (active at once) and
  `--learned-scope firm` only for a lesson that names no client. Your own finding waits as
  a candidate for review, and nothing is promoted to the firm, a rule or a skill without
  the consultant's word.
- Handoffs should let another consultant locate source, understand the current
  state, reproduce checks and take the next action without reading the whole chat.
- Record what a check rested on with `--check`, `--as-user`, `--in-org` and `--not-covered`;
  a correction is a new record with `--corrects ID`, never an edit of the old one.
- Prepared, executed, verified, accepted, published and sent are different states. A generated
  client document is not a delivered message; a saved plan is not a completed change.

Use `/session-save`, `/session-resume`, `/export-session` and `/handoff` as appropriate;
simple questions do not require empty administrative records.

For an engagement that spans sessions, an optional `torque change` record can keep
criteria, decisions, checks and next actions together. Maintain it when it reduces
repeated explanation. Local evidence copies retain their hashes; manual results
remain reported, and exact deployment observations do not prove business behavior.
