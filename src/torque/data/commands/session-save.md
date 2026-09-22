---
description: "Save a concise factual session delta and artifact references for the next session."
---

# /session-save

Save a concise factual session delta and artifact references for the next session.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

Summarize the meaningful change in this task: requested outcome, actual work,
target identity, artifact paths, validation/verification, pending work and next action.
Use exact pre-state references for recovery rather than embedding sensitive values
in the narrative. Preserve distinctions between planned, prepared, executed,
verified and incomplete; choose the record status based on the actual result.

```sh
torque session add --workspace <private-path> --client <client-name> --summary <summary-text> --status <status> --evidence <artifact-path>
```

Supported status values are `prepared`, `executed`, `verified`, `incomplete`.
Omit `--evidence` when no file exists; never fabricate a path. Inspect `--help` for
how many evidence arguments the installed version accepts. Save multiple useful
artifacts through the supported record format rather than guessing extra flags.
Session evidence references the original file and records its hash; it does not
copy that file. Keep the artifact at its recorded path. For a captured private
evidence copy tied to an acceptance criterion, use `torque change check --evidence`.

Append a new session record; do not replace previous history or copy a whole chat
transcript. Record unattended asynchronous jobs by exact job ID and unresolved
status. Save after meaningful work when continuity helps; no empty ceremonial
session entry is needed for a simple question.

For work that spans sessions, use an optional `torque change` record to retain the
business outcome and acceptance criteria. Append meaningful decisions with
`change note`, reported observations with `change check`, and exact deployment
observations with `change verify-deploy`. Use `--help` for arguments. The assistant
can maintain these records during ordinary work; do not make the user fill out a
form or add a record for every small question.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
