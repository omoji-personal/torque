---
description: "Show the complete command catalogue and choose the workflow for the task."
---

# /help

Show the complete command catalogue and choose the workflow for the task.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

Run `torque workflows list`; use `torque workflows show <name>` for a recipe.
Present commands by group and describe whether they are guided, native, or compatibility.
Natural language is the default: the user does not need to memorize commands.

Examples: “Connect this sandbox,” “Find why this Flow fails and fix it,” “Migrate these
records,” “Test as the support user,” “Undo that update,” and “Save a handoff.”
Route each to the relevant recipe and continue the authorized work. Investigation,
implementation, migration, validation, browser testing, recovery, meeting processing,
lesson memory, and documentation all remain available.

Explain the distinction between preparing, executing, and verifying when it affects
the task. Do not label browser QA read-only: a chosen user journey can write records.
Do not describe local file generation as an org write. No Torque approval token is
needed for ordinary Salesforce tools. Use the installed command help to inspect optional dependencies.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
