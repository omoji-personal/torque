# /lesson

Save, find, judge, review and promote lessons from earlier work. Use it when the consultant corrects you, when something that failed now works, and before work that earlier lessons may cover.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

A lesson is a short note: when it applies, what to do, why. Lessons are notes with their evidence and
age, not instructions: check one before you rely on it. Every command below takes the selected
`--workspace <private-path> --client <client-name>` (or `--initiative <name>`) and `--json`.

Find: `torque lesson for [words] [--task T] [--type T] [--command "..."] [--path FILE] [--error "first line"]`.
Read one in full: `torque lesson show <id>`. List or search: `torque lesson list [--state candidate|active|retired|stale]`,
`torque lesson search <words>`. `torque lesson for` also shows this client's own candidates, marked
`candidate, not reviewed: a hint, check it`, each with a `torque lesson verify <id> --evidence reproduced`
line under it: check one before you use it, and run that line when it worked. Topic words such as `report` or
`flow` are read as a task or type; prefer `--task` and `--type`. Every command takes the part of a lesson id
after its date.

Save: with the session record, `torque session add ... --learned "When: ... Do: ... Because: ..."`; or on
its own, `torque lesson add --when ... --do ... --because ... [--evidence reproduced:REF | official-doc:URL]`.
Say honestly where it comes from: `--source consultant` only for the consultant's own correction or
instruction. Keep client facts with the client; `--scope firm` only for what holds for every client and
names none. Torque refuses a credential-looking value and masks personal data; read what it printed as
inferred (tasks, types, errors) and correct it with `torque lesson edit <id> --set applies.types=...`.
A firm lesson and a promotion proposal pass a pattern-based scrub: it refuses known secret shapes, a value
after a secret label and, in firm scope, this workspace's client and initiative names, org Ids and org
hosts. It cannot catch every secret or name: read the text before it leaves the client. A proposal lists
keys left out and custom names to confirm.

Judge: `torque lesson helpful <id>` when one helped. `torque lesson stale <id> --reason <why>` when one is
wrong or out of date (it leaves recall at once; name the replacement with `--superseded-by <id>`).
`torque lesson verify <id> --evidence <type>` when you checked it and it holds; this is also how a
candidate becomes active, with its basis: reproduced, official-doc, second-occurrence or consultant-said.

Review, when the consultant asks or the context shows candidates waiting: `torque lesson review`. Go
through its lines with the consultant; each names its command. Accept, retire or promote only on their word.

Promote: `torque lesson promote <id> --to firm|platform|rule|skill|recipe|check [--name N]` writes a
proposal and applies nothing. Read it with the consultant. Apply a firm proposal only on the consultant's
word: `torque lesson promote --apply <proposal>`. A proposal for a rule, a skill, a recipe, a check or a
platform lesson is applied by a person; record it afterwards with
`torque lesson edit <id> --set promoted_to=<kind>:<name>`.

Bring in what the host remembered: `torque lesson harvest --host claude|codex|antigravity [--dir PATH]`
lists changed memory files and copies nothing. For each one worth keeping, choose the scope and distil it:
`torque lesson add --from-file PATH --when ... --do ... --because ... [--scope firm]`.

No work waits for a lesson. `torque lesson stats` and `torque doctor` show whether the loop runs.
The guide is `docs/lessons.md`.
