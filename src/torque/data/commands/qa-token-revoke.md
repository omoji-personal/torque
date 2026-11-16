---
description: "Remove an optional legacy QA skip token when requested."
---

# /qa-token-revoke

Remove an optional legacy QA skip token when requested.

**Interface:** Compatibility recipe. A slash command is an assistant instruction, not a separate shell executable.

This name is kept for command continuity. `torque qa token-revoke --help` says what a legacy QA skip
token records and that it authorizes nothing; no normal QA or execution needs one. Use it only
when the user explicitly needs compatibility with existing token records, with the selected
private workspace and client. Do not mint a token because a check is missing, unavailable,
deferred or unsuitable: state a requested skip in the QA coverage summary instead. Report the
actual compatibility action and continue the original task.

Revoke only the selected client's token.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
