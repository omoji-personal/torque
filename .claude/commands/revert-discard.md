---
description: "Mark a selected snapshot abandoned while keeping its recovery artifacts."
---

# /revert-discard

Mark a selected snapshot abandoned while keeping its recovery artifacts.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

Use the snapshot and org specified by the user:

```sh
torque revert revert discard <snapshot-id> --org <org-alias> --workspace <private-path> --client <client-name>
```

This updates snapshot lifecycle state; it does not revert the org or delete the
bundle. Verify the updated status with `/revert-show`. Keep history and exact
recovery artifacts unless deletion is separately requested. If the user merely
asks to view or preview a snapshot, do not mark it abandoned as housekeeping.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
