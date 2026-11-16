---
description: "List available operation snapshots and their actual recovery state."
---

# /revert-show

List available operation snapshots and their actual recovery state.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

Use the selected client and explicit org:

```sh
torque recover show --org <org-alias> --limit 20 --workspace <private-path> --client <client-name>
```

Report snapshot ID, operation, capture time, status, parent chain and documented automatic
recoverability; `torque recover show --help` says what a listed snapshot does and does not prove.

Do not print raw recovery values into shareable output. Use `/revert-preview` for
a specific candidate, `/revert` for an authorized restoration, and `/revert-discard`
to mark a bundle abandoned without deleting it.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
