---
description: "Resolve the intended org and relevant metadata before live work."
---

# /preflight-org

Resolve the intended org and relevant metadata before live work.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Resolve the alias from the user's request or selected client; keep an explicit target
on every org command. Through available MCP or Salesforce CLI, establish connection,
org ID, instance and relevant user. Filter auth output to avoid exposing tokens.
When needed, query `Organization` for `Id`, `Name` and `IsSandbox`; domain strings
and old configuration do not establish environment type on their own.

Compare the observed identity to the selected client's saved intent. If they disagree,
resolve the actual destination before directing a write there; continue independent
local preparation where useful. Auth/target errors are operational errors, not policy scores.

Describe the relevant objects and fields, discover namespace/package requirements,
and retrieve the current components needed for the task. New components are allowed:
distinguish “expected absent because new” from “missing dependency.” Note a useful
baseline/recovery snapshot, creating a scoped one when the requested change needs it.

Return the target, identity/type evidence, available/missing dependencies and next
action. Do not impose writable-org allowlists, terminal grants, age-based blanket
snapshot gates, or a production read-only default. Reuse checks already completed
in this task when still current; do not rerun a full audit before every command.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
