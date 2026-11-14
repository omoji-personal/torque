---
description: "Save what this session did and what it taught, so the next session starts from facts. Use it after meaningful client work, before a hand-off, and when the consultant corrects you."
---

# /session-save

Save what this session did and what it taught, so the next session starts from facts. Use it after meaningful client work, before a hand-off, and when the consultant corrects you.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

1. Write the summary: requested outcome, actual work, target org, artifact paths, what was
   checked, pending work and the next action. Use exact references (files, job IDs) for
   recovery values instead of putting them in the text. Choose the status from the actual
   result: `prepared`, `executed`, `verified` or `incomplete`.
2. Before you save, ask two questions and add each answer as a lesson: what would have saved time
   in this session, and what worked that was not obvious? Write each as
   `When: ... Do: ... Because: ...`. Add `--learned-from consultant` when it is the consultant's
   correction or instruction (it is active at once); your own finding is saved as a candidate for
   review. Use `--learned-scope firm` only for a lesson that names no client. If the session
   taught nothing, add nothing.
3. Save:

   ```sh
   torque session add --workspace <private-path> --client <client-name> --summary <summary-text> --status <status> [--evidence <artifact-path>] [--learned "When: ... Do: ... Because: ..."]
   ```

   Omit `--evidence` when no file exists; never invent a path. Check `torque session add --help`
   for how many `--evidence` values this version keeps. The record references the file and its
   hash; it does not copy it, so leave the file where it is. If the command exits 4, the session
   is recorded and a lesson was not: do not add the session again; fix the lesson's text (it held
   a credential-looking value, or a firm lesson named a client) and save it with
   `torque lesson add --session <session-id> --when ... --do ... --because ...`.
4. Mark the lessons Torque showed you in this session: `torque lesson helpful <id>` for one that
   helped, `torque lesson stale <id> --reason <why>` for one that was wrong.

Append; never rewrite earlier records or paste a transcript. Record unattended jobs by exact job
ID and status. For work that spans sessions, an optional `torque change` record keeps the outcome
and acceptance criteria: `torque change note` for decisions, `torque change check` for reported
results and captured evidence, `torque change verify-deploy` for exact deploy observations.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
