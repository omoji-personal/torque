---
description: "Continue the old update command name through the Torque update workflow."
---

# /update-jsc

Continue the old update command name through the Torque update workflow.

**Interface:** Compatibility recipe. A slash command is an assistant instruction, not a separate shell executable.

This is a compatibility name for `/update-torque`. Follow that recipe for the actual
Torque installation or checkout. It does not update the historical JusticeServer
directories, use an employer GitHub account, download a token-bearing archive or
restore the old two-clone topology.

Identify the selected installation, preserve local changes and private workspaces,
use the configured package/remote source, and verify the active copy after updating.
If the user explicitly wants maintenance of a different historical repository,
treat that named repository as a separate task with its own current instructions.
No shell executable named `update-jsc` is added by this conversational compatibility route.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
