# Lessons

A lesson is a short note from earlier work: when it applies, what to do, and why. Torque
saves lessons with the steps that already happen, finds the few that match at the point
of use, counts how they did, and helps a person review and promote them.

Lessons are notes, not instructions. Torque prints them with their source and age, and
the agent or the consultant checks them before relying on them.

## The loop in six commands

```sh
# 1. Capture, with the session record that is written anyway
torque session add --workspace . --client NAME --summary "Fixed the gift entry flow" --status executed \
  --learned "When: you compare a choice-based screen field in a flow formula. Do: compare the field to the choice resource in a decision. Because: the formula sees the label." \
  --learned-from consultant

# 2. Find again, at the start of work and before a step
torque context --workspace . --client NAME                       # the client's best lessons, and counts
torque lesson for --type Flow --task deploy --workspace . --client NAME
torque lesson for --command "sf project deploy start --metadata Flow:Gift_Entry" --workspace . --client NAME
torque lesson for --error "No such column 'Region__c' on entity 'Account'" --workspace . --client NAME

# 3. Judge
torque lesson helpful ID --workspace . --client NAME
torque lesson stale ID --reason "the formula now sees the value" --workspace . --client NAME

# 4. Review, on a cadence
torque lesson review --workspace . --client NAME

# 5. Promote, through a proposal a person reads
torque lesson promote ID --to firm --workspace . --client NAME
torque lesson promote --apply promote-ID-to-firm.md --workspace . --client NAME

# 6. See that it runs
torque lesson stats --workspace . --client NAME
torque doctor --workspace . --client NAME
```

`torque deploy` and `torque data` print the matching lessons on stderr before the step.
They never block and never fail the command. What prints there is kept rare and
specific: a lesson about a component type the step touches (from `--metadata` members,
paths or the types the caller gives), or one whose command pattern names an option or
value of this command (`*destructive*`, `--hard-delete`, `--manifest`). A task alone or a
broad pattern ("any deploy") is not enough, and a lesson that answers an error
(`applies.errors`) waits for that error unless its own pattern names an option of the
command. When nothing qualifies, nothing is printed. `torque lesson for` is not narrowed.

## Where lessons live

Scopes are folders. One lesson is one Markdown file.

| Scope | Folder | Shown for |
| --- | --- | --- |
| client | `clients/<slug>/lessons/` | that client only |
| initiative | `initiatives/<slug>/lessons/` | that initiative only |
| firm | `<workspace>/lessons/` | every client and initiative of the workspace |
| platform | shipped with Torque (`lessons/` in the source), read-only | everyone |

A command reads the selected client or initiative, the firm and the platform. It never
reads another client's lessons, events or files, under any option. The one thing read
of every client and initiative is its registration (`client.json`: name, slug, org alias;
`binding.json`: the initiative's slug), so that a
firm lesson that names a client or an initiative can be refused. Without `--client` or `--initiative` a
command reads the firm and the platform; without `--workspace`, the platform only. New lessons
go to the selected client or initiative; `--scope firm` is explicit; nothing writes into
the package.

The platform scope also holds the advisory catalogue's notes (`torque advisory`), read
through an adapter: one recall, two sources, no copy. Their ids start with `A-`. A note's
`applies` object (`tasks`, `types`, optional `errors`, as in a lesson) says what it applies
to; a note without one has its types guessed from its triggers and its tasks from its
domain and title, and answers an error through its symptom's wording. A note's
`confidence` is its evidence (`verified-live` reads as `reproduced`, `documented` as
`official-doc`, `practitioner` as `session`) and sets its source (`doc` for documented,
`harvested` otherwise). Its `updated` date is the day the catalogue entry was edited, not
a verification: it prints as `catalogue note, practitioner, updated 2026-08-01`, is
`updated` in JSON (`created` and `last_verified` stay empty), and counts only for the
recency step of the ranking.

Events (shown, helpful, stale, verified, promoted, retired, restored) are appended to
`.torque/lessons/events.jsonl` under the scope's folder: a client's events stay in the
client's folder, firm and platform events go to the workspace. An event holds a lesson
id, a time and the surface that produced it (context, for, deploy, data, advisory),
never the lesson's text or the command's. The reason of a stale mark and the note of a
helpful mark are kept with their event and scrubbed like a lesson of that scope. Counters
are derived from the events and are never stored in a lesson file.

## The file

```markdown
---
id: L-20261114-a3f29c0e
title: Ship field access with every new field
scope: client                  # client | initiative | firm | platform (must match the folder)
kind: pitfall                  # pitfall | works | convention | fact
state: active                  # candidate | active | retired
when: you are about to deploy a new custom field
do: grant field permissions in a permission set in the same deploy, then check as the intended user
because: a field created through the Metadata API has no field access, even for admins
applies:
  tasks: [deploy, field-build]
  types: [CustomField, PermissionSet]
  commands: ["sf project deploy *", "torque deploy *"]
  keywords: [field access, FLS]
  errors: ["No such column"]
evidence:
  - {type: reproduced, ref: "session 20261112T101500000000Z-0a1b2c3d4e5f", date: 2026-11-12}
source: consultant             # consultant | agent | doc | harvested
created: 2026-11-14
last_verified: 2026-11-14
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Optional body, about 15 lines at most: the error text, a minimal reproduction, one example.
```

- A person or an agent may edit the file. Keys Torque does not know are kept in the
  lesson's own scope, and their names and values are scrubbed like the rest whenever
  Torque writes the file. A copy to a wider scope (`promote`) leaves them out and the
  proposal names them.
- A file that cannot be read is skipped, named in the output and in `torque doctor`, and
  never stops a command. `README.md` in a lessons folder is not read as a lesson.
- `applies.commands` are patterns with `*`. A Torque wrapper and the command it runs count
  as the same command: `torque deploy` and `sf project deploy start`, `torque deploy-quick`,
  `-resume`, `-abort` and `sf project deploy quick`, `resume`, `cancel`, `torque apex run`,
  `torque package`, `torque data` and `sf data` (`torque data bulk upsert` is `sf data
  upsert bulk`).
- `applies.errors` are pieces of an error text, in any case, with `*` for any run of
  characters. Nothing else is special: no regular expression is built from a lesson.
- Evidence types: `reproduced`, `official-doc`, `second-occurrence`, `session`,
  `consultant-said`.

## Admission

A lesson is saved `active` when it comes from the consultant (their correction or
instruction: `--source consultant`, `--learned-from consultant`) or carries `reproduced`,
`official-doc` or `second-occurrence` evidence. Otherwise it is a `candidate`: counted in
`torque context`, listed by `torque lesson review`, and not shown at the point of use (not
in the `torque context` block, not before `torque deploy` or `torque data`). The one
exception is an explicit question: `torque lesson for` shows up to two matching candidates
of the selected client or initiative as hints, after the active lessons (see Recall). A
firm candidate is never a hint, and a candidate never leaves its client.

A candidate becomes active on a basis: `torque lesson verify ID --evidence
reproduced[:REF] | official-doc:URL | second-occurrence[:REF] | consultant-said`. The same
lesson captured again on another occasion is recorded as a second occurrence, which
admits it; captured again on the same occasion, nothing is added. Another occasion is
another day than every one the lesson's evidence names and, where both carry one,
another session record. Two session records on one day are one occasion, so an agent
cannot admit its own note by recording two sessions.

A file may be edited by hand, also to `state: active`. A client, initiative or firm
lesson counts as active only when its record carries a basis: source `consultant`, or
evidence of type `reproduced`, `official-doc`, `second-occurrence` or `consultant-said`.
Without one it is treated as a candidate: `torque lesson review` lists it under "Marked
active without a basis", `torque doctor` counts it, `promote` refuses it, and `verify`
asks for a basis. The rule is one function, `lesson.unbacked_active`.

## Capture

- `torque session add ... --learned TEXT` (repeatable), with `--learned-scope client|firm`
  (default: the selected client or initiative) and `--learned-from consultant|agent`
  (default agent). Each lesson is linked to the session record. A lesson that cannot be
  saved never loses the record: the record is written first, the problem is reported and
  the command exits 4.
- `torque lesson add [TEXT ...]` (also `capture`), with `--when`, `--do`, `--because`,
  `--title`, `--scope`, `--kind`, `--task`, `--type`, `--command`, `--keyword`, `--error`
  (repeatable), `--evidence TYPE[:REF]`, `--source consultant|agent|doc`, `--body-file`,
  `--from-file`, `--session`. Free text written as `When: ... Do: ... Because: ...` is read
  as those parts; any other text is kept as the body with its first sentence as the title.
  `-` reads standard input; without it Torque never waits on standard input.
- Applicability that is not given is taken from the text by a small vocabulary (flow,
  field, permission set, validation rule, layout, Lightning page, Apex, deploy, retrieve,
  query, data load, sandbox refresh, browser, sign-in, report, integration, rollup and
  more), including error codes and known error messages the text quotes. What was
  inferred is printed so it can be corrected: `torque lesson edit ID --set
  applies.types=Flow,CustomField`.
- `torque lesson harvest --host claude|codex|antigravity [--dir PATH] [--since DATE]` lists
  the files the host's own memory changed since the last harvest: name, type, one-line
  description, path. It copies nothing, because a host's memory mixes clients. A note
  enters a scope only through `torque lesson add --from-file PATH ...` once someone has
  chosen the scope. Started anywhere, also above the clients (`--dir` naming the
  workspace or a folder that holds several workspaces), it never enters the folder of
  another client or initiative, of this workspace or any other (a folder with a
  `client.json` or `binding.json`, or under a workspace's `clients/` or `initiatives/`),
  follows no linked folder or file, and checks each file by where it resolves before it
  opens it. The output counts what was left out. Claude Code's folder is found from the
  workspace path (`~/.claude/projects/<path with separators as dashes>/memory/`),
  Codex's as `~/.codex/memories_v2/` or `memories/`; other places need `--dir`. Where
  Codex keeps its memory in a database file (`~/.codex/memories_*.sqlite`) and no such
  folder exists, the output says so in one line: the database is not opened or read;
  export it to a folder of notes, or name one with `--dir`. For Antigravity, which keeps
  what `/learn` writes as rules and skills, the places its documentation names are read:
  `.agents/rules/*.md` and `.agents/skills/*/SKILL.md` in the workspace,
  `~/.gemini/GEMINI.md` and `~/.gemini/config/skills/*/SKILL.md`; a file Torque wrote
  there and nobody changed is left out, and the output names the places looked at.
  Antigravity's knowledge items are not documented as files and are not looked for.
  `MEMORY.md` is skipped on purpose in every host folder: it is an index of the other
  notes, not a note.

### What is refused or masked

| Scope | Refused | Masked |
| --- | --- | --- |
| every scope | credential-looking values: session and access tokens, sign-in addresses that carry one, the value of an `Authorization:` header (Basic, Bearer, Token or any scheme) and a long `Bearer` token, private keys, provider tokens, and a value after an explicit secret label (below) | email addresses, phone numbers, social security numbers, Salesforce org, user and record Ids; invisible characters are removed |
| firm | also the names, slugs and org aliases of this workspace's clients, the slugs (and so the names) of its initiatives, org Ids and org host names: My Domain, site, Visualforce and Lightning hosts, instance hosts (`na139.salesforce.com`), names under `force.com` and `documentforce.com` (Salesforce's own public hosts such as `login`, `test` and `developer` pass) | |

The same scrub runs wherever text enters a lesson or its log: `lesson add`, `session
add --learned`, `edit`, a stale reason, a helpful note, evidence references, `migrate`,
`promote` (proposal and apply). `harvest` prints a host note's line only after the same
check, and withholds it when the check would refuse it. A refusal names the class of
what was found, never the value, and exits 4.

Explicit secret labels are password, passphrase, passcode, secret, client or consumer
secret, API key, token (also access, refresh, security, session token), session id and
`sfdx auth url`. After one of them:

- an assignment (`password=...`, `password:value`, `"api_key": "..."`, `--password
  VALUE`) is refused whatever the value is made of, from four characters;
- a quoted value in prose (`The password is "..."`) is refused whatever it is made of,
  unless the quotes hold a sentence of more than four words;
- an unquoted value in prose (`the token is ...`, `password: ...`) is refused when it
  does not read as a word: six characters or more with a digit or a symbol, or twenty or
  more of anything. `The token is short-lived` and `the password is stored in the vault`
  pass.

Placeholders pass: `<value>`, `$NAME`, `%NAME%`, `{{name}}`, `****`, `...`, an
environment variable's name (`SF_PASSWORD`) and words such as `required` or `redacted`.

Identifiers. An org Id (`00D`) or a user Id (`005`) is known by its key prefix and its
length, 15 or 18 characters, whatever the rest is made of; `00DXXXXXXXXXXXX` is a
placeholder. Other record Ids have no fixed prefix, so a broader heuristic marks a 15 or
18 character token as one when it mixes three or more digits with letters and has the
padding zeros of a record number, or when its 18-character checksum holds; an ordinary
15-letter word, a short hash or a version number is left alone. In firm scope a client
name, slug or org alias of two characters is matched as registered and in capitals, on
word boundaries (`XY`, not `XYZ`); a one-character name is not matched, since it would
refuse every lesson with the word "a". A short or everyday name makes firm captures with
that word fail; the message names the class.

The scrub is a heuristic: it catches known shapes, not every secret or every name. Known
ways through, by design: an unquoted ordinary word as a password in prose (`the password
is swordfish`), a word between the verb and the value (`the password is now ...`), a
value under four characters, a secret with no label and no known token shape, a quoted
value that starts like a placeholder (`"$..."`), and anything said in an unusual way.
Firm scope knows this workspace's registered client names, slugs and aliases, not
people's names or a client's nickname. Read a lesson before it leaves its client: a
firm lesson, and every promotion proposal, is read by a person before it is applied.

## Recall

`torque lesson for [TOPIC ...] [--task T] [--type T] [--command TEXT] [--error TEXT]
[--path P] [--limit 5] [--include-candidates] [--json]` (also `recall`).

- Matching runs over every lesson the call may read, then ranks: match strength with a
  modest bonus for nearness (the selected client's or initiative's own +2, the firm's +1),
  helpful minus stale marks, then evidence (`reproduced` or `official-doc`; then
  `consultant-said` or `second-occurrence`; then `session`), then recency in coarse steps
  (verified or saved within 90 days, within a year, older), then scope, then id. A weak
  client match never pushes a strong platform match below the cut, a few days' difference
  never decides the order, and when a lesson's own error matcher meets the error, the best
  such lesson always keeps a place. With selectors a lesson must match at least one;
  when nothing matches, one line says so (with how to save a lesson) and the command
  exits 0. Precision comes first: when in doubt, nothing is shown.
- Topic words: a word the vocabulary knows is asked as its task or type (`flow` is the
  Flow type, `dashboard` the Dashboard type, `deploy` and `report` are tasks). The other
  words are compared with what a lesson is about: its title, `when`, keywords, tasks and
  types, never its `do`, `because` or body. A word matches a whole word; a plural or
  singular ending may differ (`snapshot`, `snapshots`), nothing else (`report` is not
  `reporting`). A lesson found by words alone needs a word in its title or keywords, or two
  different words. A keyword of two or more words that the topic spells out (`custom
  settings`, `screen action`) counts as much as a type.
- Commands: an org alias (`--target-org dev-sandbox`, `-o`) is a name and is not matched. A
  lesson's command pattern that names an option or a value beyond the command's name
  (`*destructive*`, `sf project retrieve start*--manifest*`) weighs more than a broad one
  (`sf project deploy *`).
- `--path` infers the metadata type from a file name, a folder or a `package.xml`
  (`*.flow-meta.xml` is Flow, `*.field-meta.xml` is CustomField, and so on). Only names
  are read, links to folders are not followed, and a path inside the folder of another
  client or initiative of the workspace is not looked into. A folder walk reads at most
  2,000 names and says so in one line when it stops there. An underscore joins a name:
  `Flow:Update_Layout_Flow` is a flow, not a layout.
- `--error` takes the first line of an error. It matches the lessons' error matchers and,
  for the advisory catalogue, a note whose title or symptom names the same error code or
  shares the error's wording.
- A lesson about other component types than the ones asked for does not match on its
  task, its command or its words.
- Not shown by default: candidates, retired lessons, lessons marked stale, and lessons
  with `promoted_to` set (they live on in their promoted form).
- Hints: after the active lessons, `torque lesson for` shows up to two matching candidates
  of the selected client or initiative (the commonest case: an agent's own "this failed,
  then that worked"). Each is marked `candidate, not reviewed: a hint, check it` and
  followed by `If it worked: torque lesson verify ID --evidence reproduced`, which makes
  it active with its basis recorded. With `--json` they are under `candidate_hints`, apart
  from `lessons`; other matching candidates are counted in `candidates_not_shown`.
  `--include-candidates` lists every matching candidate among the lessons instead.
- Each lesson prints in three lines: id and WHEN, DO and WHY, then scope, evidence type,
  age and counters. Every print appends a `shown` event; when the session id is known, a
  lesson counts once per session and surface. `shown` counts prints, not use or value.

`torque context` (and `--json`, key `lessons`) shows at most five active lessons (about
fifteen lines, read at the start of every session): the selected client's or initiative's
best, then the firm's (consultant-sourced first, each marked firm), because most standing
corrections hold for every client. The firm keeps up to two of the five places when it has
lessons and takes any the client leaves free. Then the counts for the firm and the
platform with the command to ask for more, and the candidates waiting. `torque handoff` carries the same
block. `torque advisory notes --command ... --json` is always the list of catalogue
notes; with `--with-lessons` it is an object with `platform_notes` and `scoped_lessons`
(the selected client's, the firm's and the shipped lessons; empty when no workspace is
selected). The shape depends on the option only, never on the launcher's environment.
The text output adds the scoped lessons when a client is selected.

Other packages call one function before a consequential step:
`jsc_memory.recall.for_command(command_text, *, types=(), tasks=(), paths=(), error="",
limit=3, scopes=None, via="command") -> str`. It returns the text to print or `""`, and
never raises.

## Feedback

| Command | Effect |
| --- | --- |
| `helpful ID [--note TEXT]` | counts a helpful mark |
| `stale ID --reason TEXT [--superseded-by ID]` | takes the lesson out of recall at once; its state stays as it is until `verify`, `restore` or `retire` |
| `verify ID [--evidence TYPE[:REF]]` | dates the lesson, sets the next review date, clears a stale mark, admits a candidate |
| `retire ID --reason TEXT` | keeps the file, never shows the lesson |
| `restore ID` | brings a retired lesson back, or clears a stale mark |
| `edit ID --set key=value ...` | changes fields; lists as comma-separated words. The body is edited in the file. State and source are not set here: they change through `verify`, `retire` and `restore`, which record why |
| `show ID`, `list`, `search TERMS` | read; `show pending|active|archive` still lists by state |

A new id is the date and eight random characters, chosen so that no lesson this command
can read (the selected client or initiative, the firm, the platform) has it; another
client's folder is never looked into for that. Ids of four random characters from earlier
versions keep working. Every command takes the whole id, a unique start of at least four
characters after the `L-`, or the part after the date alone (`c0de1234`) or a unique
start of it: an id starts with its date, so four characters from the front are only the
year. `add` prints that short part, and `list` says it is enough. An
empty or ambiguous id changes nothing, lists the candidates and exits 3. A platform
lesson can be marked helpful or stale in a workspace (a stale mark holds for that
workspace) and is never rewritten there. No command deletes a lesson: `retire` keeps the
file out of sight, and a person who wants it gone deletes the file.

## Review and promotion

`torque lesson review` writes a report, also saved as
`.torque/lessons/proposals/<date>.md`: candidates waiting, likely duplicates, lessons
past their review date, shown five times with no helpful mark or not shown in 180 days,
stale marks, lessons worth promoting (helpful three times, or seen a second time) and
client lessons that could serve every client. It changes nothing; each line names the
command that acts on it. A lesson file may have been edited by hand, so the report prints
each text through the same scrub as a capture: a credential-looking value withholds the
line, Ids and personal data are masked, and in a firm lesson a client's name is replaced
with a neutral word.

`torque lesson promote ID --to firm|platform|rule|skill|recipe|check [--name N]` writes a
proposal: the exact text, the destination, what the scrub replaced, the front-matter keys
Torque does not know that were left out (a wider-scope copy carries only the known keys),
the custom names (`Something__c`) in the text for a person to confirm (not refused: a
firm lesson may name fields),
and what a person must confirm. It applies nothing. `torque lesson promote --apply
PROPOSAL` performs the one promotion that stays inside the workspace, client (or
initiative) to firm: it scrubs the text between the fences again, every key in it
included, and writes that text and nothing else as the firm copy, then marks the source
`promoted_to`. For a rule, a skill,
a recipe, a check or a platform lesson the proposal is the deliverable: shared and
shipped guidance is changed by a person or a pull request, and the lesson is then
recorded with `torque lesson edit ID --set promoted_to=rule:NAME`. Apply only on the
consultant's word.

## Proving the wiring

- `torque lesson stats [--since DATE]`: captured by source and scope, events by kind,
  shown by surface, candidates waiting and the oldest one, active lessons never shown,
  and how many damaged event rows (not JSON, or a field of the wrong type) were skipped;
  `torque doctor` names that count too. A damaged row never stops a command.
- `torque doctor`: lessons by state for the firm and the selected client, unreadable
  files, the last capture and the last time a lesson was shown, and one line that says
  whether the loop is live (a lesson was saved or shown in the last 30 days) or idle.
- `torque lesson selfcheck` (run by doctor): in a temporary folder it writes a lesson,
  finds it again by type, by command, through the context block and through
  `for_command`, and reports pass or fail. Exit 6 on failure.

## Modes and hosts

- The commands are the same under Claude Code, Codex and Antigravity. Torque installs no
  hook for lessons: recall happens in `torque context`, in `torque lesson for`, and in
  the output of `torque deploy` and `torque data`.
- Build-only mode allows `torque lesson for [WORDS] --workspace .` with `--task`,
  `--type`, `--command`, `--error`, `--limit` and `--json`: the firm's lessons and the
  shipped ones, never a client's, and never candidates (`--include-candidates` is
  blocked there). The folder named must be the workspace itself. `torque lesson --help`
  and `torque lesson for --help` pass. Every other lesson command stays blocked there. The firm's lesson files are ordinary workspace
  files outside `clients/`.
- In maintenance (`.torque/maintenance`) lesson commands pause like the other packages;
  `torque context` still shows lessons and records nothing.

## Exit codes of `torque lesson`

| Code | Meaning |
| --- | --- |
| 0 | done; also: nothing matched |
| 1 | no such lesson |
| 2 | not understood, nothing to save, or nowhere to keep it |
| 3 | the id is empty, too short or matches several lessons (they are listed) |
| 4 | refused by the scrub: a credential-looking value, or a firm lesson that names a client |
| 5 | read-only: the lesson ships with Torque |
| 6 | a check failed (`selfcheck`) |
| 7 | busy: another Torque command holds the lessons lock; run the command again |

Every command takes `--json` and never prompts.

## Writing a platform lesson

Platform lessons live in `lessons/*.md` in the Torque repository and are bundled by
`python scripts/sync-workflows.py` into `src/torque/data/lessons/`.

- `scope: platform`, `state: active`, a stable `id` (`L-YYYYMMDD-xxxxxxxx`), one file per
  lesson with a descriptive file name.
- Fully generic: no client, person, org or firm practice. `when`, `do` and `because` on
  one line each; the body holds the check.
- Evidence stated honestly: `official-doc` with the URL only where a source says it;
  otherwise `{type: session, ref: "field-reported, one org"}` and a `review_by` about
  three months out. Leave `last_verified` empty until someone re-tests it.
- Give `applies` by hand: tasks, types, command patterns, keywords and, where the lesson
  answers an error, error matchers.

## From the earlier store

`torque lesson migrate --workspace . --client NAME` imports the earlier JSON lessons
(`state/memory/lessons/`) of the selected client into this format, once: pending lessons
become candidates, active ones stay active, archived ones are retired. The old files are
left in place. The hook-driven capture, spool, ranker, index and injector of the earlier
package had no caller and were removed.

## Limits

- Matching is by words, types, commands and error text. A lesson with poor `applies`
  values is found less often; `torque lesson stats` lists the active ones never shown.
- The events log grows by one line per lesson shown and is read on every recall.
- A stale mark and the counters live in the events log. Deleting the log clears them;
  the lessons themselves are unaffected.
- Nothing captures a lesson without being asked: no hook watches for a failure followed
  by a fix. The capture path is `--learned`, at the step that records the session.
