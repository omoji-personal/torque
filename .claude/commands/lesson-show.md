---
description: "Read relevant saved lessons and distinguish active, pending and archived records."
---

# /lesson-show

Read relevant saved lessons and distinguish active, pending and archived records.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

Use the selected client and the requested state:

```sh
torque lesson show pending --workspace <private-path> --client <client-name>
torque lesson show active --workspace <private-path> --client <client-name>
torque lesson show archive --workspace <private-path> --client <client-name>
```

Read only relevant records. Show ID, concise lesson, scope, source/date and lifecycle
state. A pending queue is an optional working aid, not an action gate or evidence
that past sessions failed. Active means retained as useful, not verified-current.
Recheck drift-prone facts before using them for a current operation. Do not load
every other client's lessons into a shared model context.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
