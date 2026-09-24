---
description: "Turn a stakeholder requirements note into user stories, a metadata list, a test script and open questions."
---

# /requirements-to-build

Turn a stakeholder requirements note into user stories, a metadata list, a test script and open questions.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Start from the actual note: audience, current process, the change being asked for,
and any constraint or deadline stated or implied. Read it for what is actually
requested versus what is merely described as current pain; do not add scope the
stakeholder did not ask for.

1. Write user stories in the form "As a [role], I want [capability], so that
   [outcome]." One story per distinct capability; split a compound request instead
   of writing one story that hides two behaviors.
2. Attach acceptance criteria to each story: the observable behavior that proves it
   done, including at least one negative or edge case (missing data, duplicate
   submission, a user without the expected permission).
3. Produce the metadata list: objects, fields (with type and whether required),
   automation (Flow, validation rule, or Apex, with a one-line reason for the
   choice), and any permission-set or page-layout change implied by the stories.
   Mark anything not confirmed against the actual org as proposed, not existing,
   metadata.
4. Write a test script: one row per acceptance criterion, the exact steps, the
   expected result, and space for the actual result and status. Include the
   negative and edge cases from step 2.
5. List open questions: anything the note left ambiguous (exact field values, who
   approves, what happens to existing records) that changes the design depending on
   the answer. Do not guess a default and present it as the stakeholder's decision.

Keep the requirements, the proposed metadata and the accepted decisions distinct.
When scope is agreed, hand off to `/prep-changeset` and `/validate-change` rather
than treating this recipe as a second implementation path. Save the requirements
and open questions to the client session.

## In build-only mode

When the workspace's `ai_access` is `build-only`, the agent cannot reach an org or read
`clients/`. It works only from material the consultant supplies with names, IDs and values
removed, for example a redacted stakeholder note. Every step above that needs the live org, a
record or the client session becomes an explicit hand-off: the agent says what the
consultant should check, run or record, and marks each conclusion that depends on it as
unconfirmed. Here, resolving the open questions with the stakeholder, checking existing
metadata, and saving the requirements to the client session are hand-offs.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
