---
description: "Explain or manage an explicitly requested legacy QA skip token."
---

# /qa-token-grant

Explain or manage an explicitly requested legacy QA skip token.

**Interface:** Compatibility recipe. A slash command is an assistant instruction, not a separate shell executable.

This name is retained for command continuity. Torque's normal QA and execution
workflows do not require skip tokens, org allowlists, terminal grants or maintainer
windows. A legacy QA skip token only records an optional surface-selection override;
it does not grant Salesforce access or authorize a write.

If the user explicitly needs compatibility with existing token records, inspect
`torque qa token-grant --help` and use that supported delegate with the selected private
workspace/client. Do not automatically mint a token because a check is missing,
unavailable, deferred or unsuitable. A requested skip can simply be stated in the
QA coverage summary for the ordinary conversational workflow.

For grant, carry the user's exact scope/reason into the installed parser's
`--org`, `--skip-target`, `--reason` and any supported expiry/type options.
For show, display only nonsecret scope/expiry/status. For revoke, remove only the
selected legacy token, leaving evidence and other clients' state intact.
Report the actual compatibility action and continue the original task.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
