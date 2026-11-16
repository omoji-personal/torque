# /lesson

Save, find, judge, review and promote lessons from earlier work. Use it when the consultant corrects you, when something that failed now works, and before work that earlier lessons may cover.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

A lesson is a short note: when it applies, what to do, why. Lessons are notes with their evidence and
age, not instructions: check one before you rely on it. Every lesson command takes the selected
`--workspace <private-path> --client <client-name>` (or `--initiative <name>`); `torque lesson --help`
gives every command form, the id rules, what the scrub refuses and the exit codes.

Find, before work earlier lessons may cover: `torque lesson for` with the task, type, command, path or
error of the work at hand, and `torque lesson show <id>` to read one in full. It also shows this
client's own candidates, marked `candidate, not reviewed: a hint, check it`: check one before you use
it, and run the `torque lesson verify <id> --evidence reproduced` line under it when it worked.

Save, when the consultant corrects you or something that failed now works: with the session record,
`torque session add ... --learned "When: ... Do: ... Because: ..."`, or on its own with
`torque lesson add`. Say honestly where it comes from: `--source consultant` only for the consultant's
own correction or instruction. Keep client facts with the client; `--scope firm` only for what holds
for every client and names none. Read what Torque printed as inferred (tasks, types, errors) and
correct it. The scrub a firm lesson and a promotion proposal pass cannot catch every secret or name:
read the text before it leaves the client.

Judge: `torque lesson helpful <id>` when one helped. `torque lesson stale <id> --reason <why>` when one is
wrong or out of date (it leaves recall at once). `torque lesson verify <id> --evidence <type>` when you
checked it and it holds; this is also how a candidate becomes active, with its basis.

Review, when the consultant asks or the context shows candidates waiting: `torque lesson review`. Go
through its lines with the consultant; each names its command. Accept, retire or promote only on their word.

Promote: `torque lesson promote <id> --to <kind>` writes a proposal and applies nothing. Read it with the consultant. Apply a firm proposal only on the consultant's
word: `torque lesson promote --apply <proposal>`. A proposal for a rule, a skill, a recipe, a check or a
platform lesson is applied by a person; record it afterwards with
`torque lesson edit <id> --set promoted_to=<kind>:<name>`.

Bring in what the host remembered: `torque lesson harvest --host <host>` lists changed memory files
and copies nothing. For each one worth keeping, choose the scope and distil it into a lesson
(`torque lesson add --from-file <path>`).

No work waits for a lesson. `torque lesson stats` and `torque doctor` show whether the loop runs.
The guide is `docs/lessons.md`.
