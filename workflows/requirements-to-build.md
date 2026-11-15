# /requirements-to-build

Turn a stakeholder requirements note into user stories, a metadata list that starts from what the org already has, a test script and open questions. Use it when a request arrives as prose and has to become buildable work.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Read skill `salesforce-build` first (`salesforce-build/references/choosing-the-tool.md` holds the
ladder). Start from the actual note: audience, current process, the change asked for, and any
constraint or deadline stated or implied. Separate what is requested from what is described as
current pain; do not add scope the stakeholder did not ask for.

1. Write user stories in the form "As a [role], I want [capability], so that [outcome]." One
   story per distinct capability; split a compound request instead of hiding two behaviors in one.
2. Give each story acceptance criteria: the observable behavior that proves it done, with at least
   one negative or edge case (missing data, a duplicate submission, a user without the permission).
3. Resolve every object, field and value the note names against the org before you design:
   describe each object (`sf sobject describe --sobject <Object> --target-org <org-alias> --json`)
   and record the API name, or "does not exist". Count real data variety where a rule depends on
   it.
4. Write the native baseline first: the options in order (standard feature, installed package,
   declarative configuration, code) with hours beside each, and take the first that meets the
   criteria. When you step up to a flow or to code, record what the simpler option could not do.
5. Produce the metadata list: objects, fields (type, required or not), automation (flow,
   validation rule or Apex, each with its one-line reason), and any permission set, page layout or
   Lightning record page (Dynamic Forms) change the stories imply. Mark everything not confirmed
   in the org as proposed.
6. Write a test script: one row per acceptance criterion with the exact steps, the expected
   result, and space for the actual result and status, including the negative and edge cases. Name
   the user each row runs as.
7. List open questions: anything the note left ambiguous (exact values, who approves, what happens
   to existing records) that changes the design. Do not guess a default and present it as the
   stakeholder's decision.

Restate the request in a short outline and get the stakeholder's yes before building. Keep the
requirements, the proposed metadata and the accepted decisions apart. When scope is agreed, hand
off to `/prep-changeset` and `/validate-change`. Save the requirements and open questions to the
client session.

## In build-only mode

When the workspace's `ai_access` is `build-only`, the agent cannot reach an org or read
`clients/`. It works only from material the consultant supplies with names, IDs and values
removed, for example a redacted stakeholder note. Every step above that needs the live org, a
record or the client session becomes an explicit hand-off: the agent says what the
consultant should check, run or record, and marks each conclusion that depends on it as
unconfirmed. Here, resolving names against the org (step 3), resolving the open questions with
the stakeholder, and saving the requirements to the client session are hand-offs.
