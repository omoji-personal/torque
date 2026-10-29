# Updating a workspace's packaged workflows

After installing a newer Torque version, refresh an existing private workspace:

```sh
torque workspace upgrade /path/to/private-workspace --check
torque workspace upgrade /path/to/private-workspace
```

The first command previews changes without writing files or creating a lock file.
The second applies updates to unchanged defaults and adds missing packaged files.
Both use the bundle in the installed Torque package; neither downloads anything
or contacts Salesforce or an AI provider. Each prints one line of counts, then only the
files that need something, each with the command that compares or keeps it. `--json`
gives the whole report instead. The exit code is 0 whatever the report holds (3 only
when a missing gate hook could not be added); read `conflicts` in the JSON to act on them.

Only packaged Markdown files in these locations participate:

- `.claude/commands/`, `.claude/rules/`, `.claude/skills/`, `.claude/agents/`
- `.agents/skills/` (a separate copy of the packaged skills)
- `.agents/rules/`, `.agents/agents/` and one `.agents/skills/NAME/SKILL.md` per recipe:
  the [Antigravity copies](#antigravity-copies)

Client and initiative folders (configurations, evidence, state, sessions),
profiles, and root `AGENTS.md` files remain outside this updater's scope. Other locally added workflow files
also remain untouched. The one root file it changes is a `CLAUDE.md` that an earlier
version wrote and nobody edited: it asked the model to read `AGENTS.md`, and is replaced
by the `@AGENTS.md` import (`update`), because with both files present Claude Code reads
only `CLAUDE.md`. An edited `CLAUDE.md` is kept; when it does not import `AGENTS.md`,
the report says so under `claude_md`.

## What the result means

| Action | Behavior when applying |
| --- | --- |
| `current` | Already matches the installed default and recorded version. |
| `adopt` | Matches the installed default; record its hash and version. |
| `add` | Install a missing packaged file. This also restores a deleted default. |
| `update` | Replace a previously recorded default whose local bytes remain unchanged. |
| `customized` | Keep a local edit of a file whose packaged text has not changed since it was recorded. Nothing to merge, so not a conflict. |
| `renamed` | Move a Claude Code recipe to its new name (see [Claude Code copies](#claude-code-copies)). |
| `preserve` | A conflict: an edit of a file whose packaged text also changed, a local file Torque never recorded, or a file that changed during this run. It is kept. |
| `retired` | No longer in the installed bundle; leave the file and its previous record alone. |

The JSON includes per-file actions, counts, and `conflicts` with reasons and
suggestions. `applied: false` in a check result means the action is only proposed.
An applied `adopt` updates tracking, without rewriting the workflow. A conflict
does not stop unrelated default updates.

Files are compared line endings aside: a managed file that Git (with `core.autocrlf`) or
a Windows editor saved with CRLF is not a local edit, and an untouched one still takes
packaged updates. Files are written with LF.

In a build-only workspace the upgrade also adds the gate hook when it is missing, for
Claude Code and Antigravity (`--no-hook` skips this). In a full workspace it only reports
a missing hook; `torque workspace ai-access full --path PATH` adds it
([ai-access](ai-access.md#setting-the-mode)). In both, a Claude Code gate entry that Torque
wrote in shell form is converted to exec form for the same interpreter; an entry you wrote
yourself is left as it is.

The `.torque/templates.json` manifest records the last installed or adopted
packaged SHA-256 hash and Torque version for each managed file. A local edit
keeps its prior baseline; the updater never records the edit as a new packaged
default. New workspaces record their matching defaults during initialization.

Older workspaces without a manifest can use the same command. Matching files
are adopted, missing files are installed, and different files remain unmanaged
local customizations. The updater does not infer a baseline from the workspace's
age or from a similarly named workflow.

## Claude Code copies

Claude Code answers `/help`, `/context`, `/status`, `/security-review` and `/undo` itself.
A project skill of the same name replaces a built-in command in a terminal session, but
not an alias: `/undo` stays an alias of `/rewind`, a checkpoint rewind that does not touch
an org. So those five recipes are installed as `.claude/commands/torque-NAME.md`
(`/torque-help`, `/torque-context`, `/torque-status`, `/torque-security-review` and
`/torque-undo`), and the recipes, rules and worker roles that mention them name them that
way. `torque workflows show NAME` keeps the catalogue names.

An upgrade moves an older workspace's copies: an untouched `.claude/commands/NAME.md` is
removed once `torque-NAME.md` is installed; an edited one is moved to the new name as it
is, with its recorded base, and then shows as a conflict to review, because the packaged
text changed with the rename. When both names already hold a file, both stay and the old
one is a conflict.

## Antigravity copies

Antigravity (`agy`) reads `AGENTS.md` and `.agents/` in the working folder and never
reads `.claude/`. `torque workspace init` and `torque workspace upgrade` therefore write
a second form of the packaged rules, recipes and worker roles, derived from the same
packaged files (`src/torque/antigravity.py`), so nothing is generated by hand:

| Packaged file | Antigravity copy | Difference |
| --- | --- | --- |
| `rules/NAME.md` | `.agents/rules/NAME.md` | Header `trigger: always_on` and a `description` (the rule's heading). A rule without a valid trigger is discarded by Antigravity. |
| `commands/NAME.md` | `.agents/skills/NAME/SKILL.md` | Header `name` and `description`; the skill is also the `/NAME` command. `$ARGUMENTS` is replaced by a sentence, because the typed text arrives as the message and is not expanded. |
| `agents/NAME.md` | `.agents/agents/NAME.md` | A `tools:` list in Antigravity's tool names, `mainAgent: false`, `subagent: true`. A role that names no tools gets the seven names seen working; an unknown name is dropped, because it can hang the worker. |

Antigravity answers `/help`, `/context` and `/undo` itself, so those three recipes are
installed as `/torque-help`, `/torque-context` and `/torque-undo`. A recipe without a
`description` and a rule scoped with `paths:` get no copy.

The copies are tracked in the same `.torque/templates.json` manifest, by their
workspace path, with the same actions as every other managed file. Their
`packaged_source` names the packaged file they come from, followed by
`(Antigravity copy)`. An existing workspace gets them as `add` on its next upgrade. A
file already at one of these paths with other content, for example one generated by a
local script, is a `preserve` conflict and is left alone; delete it to take the packaged
copy, or keep it with `--accept`. A copy Torque installed and a local script later
rewrote is `customized` while its packaged text is unchanged.

Torque up to 2.0.0a19 rejects a manifest whose `files` names `.agents/rules/` or
`.agents/agents/`. Entries for those two locations are therefore kept in a separate
`antigravity_files` section with the same shape, which those versions ignore, so a
workspace can be used with both. If such a version rewrites the manifest it drops that
section; the next upgrade with a current version adopts the unchanged copies again.

A build-only workspace also gets `build-only.md`, and a connected one
`production-approval.md`, as a rule for each host. `torque workspace ai-access` writes the
rule of the new mode and removes the old one; `upgrade` adds a missing one and keeps an
edited one. The two exceptions are listed under [mode rules](hosts.md#mode-rules).

Antigravity's hook file (`.agents/hooks.json`) gets the gate's entry from
`torque workspace init`, `torque workspace ai-access full|build-only`, and from an upgrade
of a build-only workspace where it is missing; see
[wiring the Antigravity hook](ai-access.md#wiring-the-antigravity-hook).
Without it, build-only mode is not enforced in an Antigravity session, and in full mode
nothing keeps its tools from changing consent, approval and control records; `torque doctor`
says so.
The formats above were observed with Antigravity CLI 1.3.1 on Windows; no Antigravity
session is part of the automated tests. Antigravity 1.4.9 still lists those seven names among
its tools when a session starts (seen 2026-10-28), although its calls name the file tools
`read_file`, `write_file` and `edit_file`. Worker roles keep the names seen working; a worker
that lists the new names has not been tried.

## Reading the selected workspace's workflow

From the private workspace, use `torque workflows show qa --workspace .` to read the
copy the workspace's host runs, including preserved customizations: its local
`.claude/commands/qa.md` for Claude Code, or `.agents/skills/qa/SKILL.md` when
`workspace.json` names `"host": "antigravity"`. For a recipe the host installs as
`torque-NAME`, that copy is read first, then `NAME` of a workspace not yet upgraded. A
missing local recipe falls back to the packaged reference, with the names that host uses. The existing `TORQUE_WORKSPACE`
selection also applies; an explicit `--workspace` takes precedence. Without either
selection, the command shows the installed recipe. Catalogue `--json` output keeps
its existing metadata format. This does not contact an org or an AI provider.

Newly generated `AGENTS.md` files describe this precedence. Existing private
`AGENTS.md` files remain untouched by upgrade; use the same local-first command
when incorporating newer guidance into an existing workspace.

## Keeping a local customization

An edit of a file whose packaged text has not changed is `customized`: kept, and not
reported as a conflict. When the packaged text changes too, the file becomes a conflict.
Compare it with the packaged text, which writes nothing:

```sh
torque workspace upgrade /path/to/private-workspace --diff .claude/commands/qa.md
```

Merge what you want in your editor. Then either restore the exact packaged default (the
next run adopts that match), or keep your version:

```sh
torque workspace upgrade /path/to/private-workspace --accept .claude/commands/qa.md
```

`--accept` records the packaged text as the base of your file, writing only the manifest:
the file is `customized` until the packaged text changes again. It also takes a local
file Torque never recorded, and a folder (`--accept .agents`) to keep every differing
managed file in it, for example copies a local script generates. The two skill copies are
tracked independently, so changing one does not change the other.

## Interrupted or simultaneous updates

Files are published atomically one at a time, followed by an atomic manifest
write. The whole batch is not a single transaction. If a run fails, its error
does not mean earlier file updates were rolled back. Rerun after resolving the
filesystem problem: already installed matching files are adopted, and remaining
defaults are updated. No incomplete manifest is written. Temporary files can
remain after a forced process termination; their `.torque-update-` prefix keeps
them outside the managed Markdown files.

A new file is published with a hard link, which cannot replace a file that appeared in
the meantime. A filesystem without hard links (Google Drive for desktop, exFAT, some
network shares) refuses the link; there Windows renames the finished file, which never
replaces an existing one, and other systems claim the name with an exclusive create and
move the finished file onto that empty claim. An existing file is still never replaced.

On supported macOS and Linux systems, a short workspace-local `flock` serializes
cooperating updaters. Checks take a shared lock when one already exists; a check
without an existing lock is a read-only snapshot and can become stale. Finish
editing the workflow files before applying an upgrade: ordinary editors do not
participate in that lock. The updater rechecks content before replacement and
atomically refuses to overwrite a newly appearing file, but it cannot provide a
transaction with an unrelated editor writing the same file at that instant.

The maintenance flag (`.torque/maintenance`) pauses record and configuration writes,
not this updater, so an administrator can upgrade workflows while records are paused.

Workflow and tracking paths must use real directories and regular files.
Symlinks are refused. An invalid tracking manifest produces an error rather than
being silently replaced; preserve it while correcting the file. Updates create
private tracking directories and files with restrictive permissions.
