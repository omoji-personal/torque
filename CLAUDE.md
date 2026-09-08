# Torque

Read `AGENTS.md` for the shared operating contract. Use `.claude/commands/`
for conversational workflows and `torque workflows` for the catalogue.

Torque is the reusable framework. Work in the selected private firm's workspace
and load only the requested client's context. Use `torque context` and the
client's notes to resume work; do not scan other clients to answer a scoped task.

The normal Salesforce CLI, configured connectors, and browser remain available.
Torque's advisory, QA routing, snapshots, and recovery tools support those
workflows. They do not create another authorization system.
