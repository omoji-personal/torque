# /session-save

Save what this session did, what each check rested on and what it taught, so the next session starts from facts. Use it after meaningful client work, before a hand-off, and when the consultant corrects you.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

1. Write the summary: requested outcome, actual work, target org, artifact paths, what was
   checked, pending work and the next action. Use exact references (files, job IDs) for
   recovery values instead of putting them in the text. Choose the status from the actual
   result: `prepared`, `executed`, `verified`, `incomplete`, or `accepted` (below).
2. Add what the session leaves behind in the org under "Left in the org" in the summary: each
   test record by Id (or the private Id file that lists them) and each temporary setting with its
   original value, so the next session can clean up. Write "Left in the org: nothing" when that is
   true.
3. Say what each check rested on. With `--status verified`, give how it was checked (`--check`,
   repeatable: `read-back`, `scripted`, `rendered`, `live`, `reviewed`), as whom (`--as-user`: a
   role, never a person's data), in which org (`--in-org`) and what it did not cover
   (`--not-covered`); Torque prints a hint naming the parts left unstated. A read-back of what you
   wrote is not a check of behaviour: record it as `--check read-back`. Acceptance is the other
   person's word, not yours: use `--status accepted` only when someone else said yes, with
   `--accepted-by <who>`.
4. Before you save, ask two questions and add each answer as a lesson: what would have saved time
   in this session, and what worked that was not obvious? Write each as
   `When: ... Do: ... Because: ...`. Add `--learned-from consultant` when it is the consultant's
   correction or instruction (it is active at once); your own finding is saved as a candidate for
   review. Use `--learned-scope firm` only for a lesson that names no client. If the session
   taught nothing, add nothing.
5. Save:

   ```sh
   torque session add --workspace <private-path> --client <client-name> --status <status> --summary <summary-text> [--check read-back|scripted|rendered|live|reviewed] [--as-user <role>] [--in-org <org-alias>] [--not-covered <what>] [--evidence <file>] [--change <change-id>] [--corrects <session-id>] [--learned "When: ... Do: ... Because: ..."]
   ```

   Never invent an evidence path, and leave each evidence file where it is. When an earlier record
   was wrong, add a new one with `--corrects <its id>`; never edit the old file. For a long summary,
   pass the fields in a file with `--input <file>`. Read what the command prints before you run it
   again: `torque session add --help` says what each exit code means (exit 4: the record is saved
   and only a lesson was refused; save that lesson with the line it printed).
6. Mark the lessons Torque showed you in this session: `torque lesson helpful <id>` for one that
   helped, `torque lesson stale <id> --reason <why>` for one that was wrong. If a candidate hint
   fixed something, verify it: `torque lesson verify <id> --evidence reproduced` (it becomes
   active for this client, with its basis).

Append; never rewrite earlier records or paste a transcript. Record unattended jobs by exact job
ID and status. For work that spans sessions, an optional `torque change` record keeps the outcome
and acceptance criteria: `torque change note` for decisions, `torque change check` for reported
results and captured evidence, `torque change verify-deploy` for exact deploy observations.
