---
description: "Preview a specific restoration against current state without executing it."
---

# /revert-preview

Preview a specific restoration against current state without executing it.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

Resolve snapshot ID and intended org from the request or `/revert-show`:

```sh
torque revert revert preview <snapshot-id> --org <org-alias> --workspace <private-path> --client <client-name>
```

Inspect original operation, snapshot completeness, target binding, exact recovery
artifacts and current drift. Explain what the inverse would change and what it
cannot restore. Missing, changed, renamed or unreadable components require a
specific reconciliation; an unknown drift result is not unchanged state.

Present proposed inverse operations, affected scope and current evidence. Preview
does not execute a restore. It is a useful operational check, not a new permission
ceremony: if the user already asked to revert this exact change, continue through
the applicable supported restore after resolving material mismatches.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
