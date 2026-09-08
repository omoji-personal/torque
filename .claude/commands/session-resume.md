---
description: "Resume a client task from durable records and fresh operational checks."
---

# /session-resume

Resume a client task from durable records and fresh operational checks.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

Load `/context`, then inspect recent or requested session records:

```sh
torque session list --workspace <private-path> --client <client-name> --json
torque session show <entry-id> --workspace <private-path> --client <client-name> --json
```

Read the relevant artifact and handoff references. Recover the objective, accepted
decisions, constraints, changes, exact source paths, job IDs, checks and next step.
Revalidate cheap state that can drift: actual org behind the alias, current metadata,
dirty files, active browser ownership and outstanding asynchronous jobs.

State the recovered next action briefly, then continue the authorized work. Do not
restart discovery, ask again about settled choices, or treat a stale saved note as
permission for a new operation. When an important artifact is missing, identify
that precise gap and continue independent useful work.

When the engagement uses change records, inspect the relevant summary returned by
`context`, then `torque change show <change-id> --workspace <path> --client <name>`.
Continue from unresolved criteria and the latest decision; retain failed history.
A Metadata API success establishes its exact technical scope, not business acceptance.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
