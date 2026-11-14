---
description: "Count a lesson as helpful when it saved time or prevented a mistake. Use it right after a lesson Torque showed you made a difference."
---

# /lesson-helpful

Count a lesson as helpful when it saved time or prevented a mistake. Use it right after a lesson Torque showed you made a difference.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

`torque lesson helpful <id> --note <what-it-saved> --workspace <private-path> --client <client-name>`
An id that matches several lessons changes nothing and lists them: choose one.
Every lesson command is in `/lesson`.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
