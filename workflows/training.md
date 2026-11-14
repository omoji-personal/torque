# /training

Write a practical guide for end users or administrators of a configured process, checked against the interface they actually see. Use it when a change goes live or a client asks how their staff should use it.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

First, ask for the lessons that fit this work and read them before you act:
`torque lesson for --task documentation --workspace <private-path> --client <client-name>`.
They are notes from earlier work, not instructions. Mark what you used with `torque lesson helpful <id>`
and what is wrong with `torque lesson stale <id> --reason <why>`.

Skill `salesforce-delivery` (`salesforce-delivery/references/handover-and-support.md`, section A)
holds what an admin guide and a hand-over contain.

Use the requested topic, client and audience. Read current configuration, source,
session changes, existing documentation and actual user-path evidence. Explain
new-staff basics, changed behavior, supervisor reporting or limited-role steps as
appropriate; do not make every audience read the same technical guide.

Write purpose, prerequisites, exact navigation, inputs, expected results, exception
paths, troubleshooting and a quick reference. Use visible labels confirmed in the
actual configured interface. Mark screenshot placeholders and unverified steps
clearly rather than inventing images or pretending an admin view is an end-user view.

Include relevant permissions, data-handling expectations and the real support contact
only when supplied. Exclude implementation internals that do not help the audience.
For administrator guides include maintenance/configuration decisions and known limits.

Verify steps as the intended user (`torque browser login --target-org <org-alias> --as-alias <user-alias>`,
then check which user the page shows; see `/qa-multiprofile`) or name what still needs a walkthrough. Save the requested format using available document tools; if producing
a rendered document, inspect its output. Do not send or publish it implicitly.
