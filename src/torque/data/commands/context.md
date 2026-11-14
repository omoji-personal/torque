---
description: "Load the selected client's configuration, recent sessions, change records and lessons from earlier work. Use it at the start of client work and whenever the client changes."
---

# /context

Load the selected client's configuration, recent sessions, change records and lessons from earlier work. Use it at the start of client work and whenever the client changes.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

```sh
torque context --workspace <private-path> --client <client-name> --json
```

The output starts with this client's lessons from earlier work; read them, and ask for the ones
that fit the task with the `torque lesson for` command it prints. Then read the requirements,
decision notes and change files the output points to, only as far as the present task needs.

Summarize the engagement, intended org, active change, known constraints and next step. Recheck
what drifts: the org behind the alias, current metadata, installed tools, deployment state. A
selected alias states intent; the identity check says where an operation will actually go.

The output also lists change summaries. For the current change,
`torque change show <change-id> --workspace <private-path> --client <client-name> --json`
returns its criteria, failed checks, decisions, metadata observations and next steps.

When the user switches client, select the new one explicitly and keep the other client's
records out of tool inputs.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
