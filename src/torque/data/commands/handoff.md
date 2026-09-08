---
description: "Produce a resumable handoff from the client history and current task evidence."
---

# /handoff

Produce a resumable handoff from the client history and current task evidence.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

Read selected client context and relevant session/artifact records, prioritizing the
current task rather than dumping every file. Ensure current work has a factual session
entry. Generate the native Markdown starting point:

```sh
torque handoff --workspace <private-path> --client <client-name> --output <handoff-path>
```

Without `--output` the command prints Markdown. Review the result and expand it with
actual artifact evidence when the engagement needs more detail. Include objective,
scope, current environment identity, implemented changes, exact source/diff references,
job/test evidence, unfinished work, recovery references, known issues, decisions,
maintenance tasks and the next executable step.

For client-facing delivery, use the client's approved naming/support contacts and
plain language; avoid internal credentials, private recovery payloads and other
clients' context. Do not invent branding or a support service. Distinguish generated
summary from checked facts and unperformed validation.

Verify every referenced path exists and that another person can locate the relevant
project/context. Saving is not sending, deploying or completing a pending job.

For one change, `torque change handoff <change-id> --workspace <path> --client <name>`
provides a focused report of criteria, decisions, technical observations and next
steps. The client handoff includes these records alongside the session journal.
Manual checks remain operator-reported, even when a captured file is attached.
Check evidence integrity and scope before using it to support client-facing claims.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
