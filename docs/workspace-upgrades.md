# Updating a workspace's packaged workflows

After installing a newer Torque version, refresh an existing private workspace:

```sh
torque workspace upgrade /path/to/private-workspace --check --json
torque workspace upgrade /path/to/private-workspace --json
```

The first command previews changes without writing files or creating a lock file.
The second applies updates to unchanged defaults and adds missing packaged files.
Both use the bundle in the installed Torque package; neither downloads anything
or contacts Salesforce or an AI provider.

Only packaged Markdown files in these locations participate:

- `.claude/commands/`, `.claude/rules/`, `.claude/skills/`, `.claude/agents/`
- `.agents/skills/` (a separate copy of the packaged skills)

Client configurations, evidence, state, sessions, profiles, and root `AGENTS.md`
files remain outside this updater's scope. Other locally added workflow files
also remain untouched.

## What the result means

| Action | Behavior when applying |
| --- | --- |
| `current` | Already matches the installed default and recorded version. |
| `adopt` | Matches the installed default; record its hash and version. |
| `add` | Install a missing packaged file. This also restores a deleted default. |
| `update` | Replace a previously recorded default whose local bytes remain unchanged. |
| `preserve` | Keep a local customization or a file that changed during this run. |
| `retired` | No longer in the installed bundle; leave the file and its previous record alone. |

The JSON includes per-file actions, counts, and `conflicts` with reasons and
suggestions. `applied: false` in a check result means the action is only proposed.
An applied `adopt` updates tracking, without rewriting the workflow. A conflict
does not stop unrelated default updates.

The `.torque/templates.json` manifest records the last installed or adopted
packaged SHA-256 hash and Torque version for each managed file. A local edit
keeps its prior baseline; the updater never records the edit as a new packaged
default. New workspaces record their matching defaults during initialization.

Older workspaces without a manifest can use the same command. Matching files
are adopted, missing files are installed, and different files remain unmanaged
local customizations. The updater does not infer a baseline from the workspace's
age or from a similarly named workflow.

## Reading the selected workspace's workflow

From the private workspace, use `torque workflows show qa --workspace .` to read
its local `.claude/commands/qa.md`, including preserved customizations. A missing
local recipe falls back to the packaged reference. The existing `TORQUE_WORKSPACE`
selection also applies; an explicit `--workspace` takes precedence. Without either
selection, the command shows the installed recipe. Catalogue `--json` output keeps
its existing metadata format. This does not contact an org or an AI provider.

Newly generated `AGENTS.md` files describe this precedence. Existing private
`AGENTS.md` files remain untouched by upgrade; use the same local-first command
when incorporating newer guidance into an existing workspace.

## Keeping a local customization

For a preserved file, compare the local content with the reported
`packaged_source`, merge useful changes in your editor, and rerun the command.
For example, using the Python interpreter from your Torque installation:

```sh
python -c 'from importlib.resources import files; print(files("torque").joinpath("data/commands/COMMAND.md").read_text(encoding="utf-8"), end="")'
```

Replace `COMMAND.md` with the reported path beneath `torque/data/commands/`.
The same approach works for rules, skills, and agents. If you choose to restore
the exact packaged default, the next run adopts that match. If you keep your
customization, future runs continue to preserve it. The two skill copies are
tracked independently, so changing one does not change the other.

## Interrupted or simultaneous updates

Files are published atomically one at a time, followed by an atomic manifest
write. The whole batch is not a single transaction. If a run fails, its error
does not mean earlier file updates were rolled back. Rerun after resolving the
filesystem problem: already installed matching files are adopted, and remaining
defaults are updated. No incomplete manifest is written. Temporary files can
remain after a forced process termination; their `.torque-update-` prefix keeps
them outside the managed Markdown files.

On supported macOS and Linux systems, a short workspace-local `flock` serializes
cooperating updaters. Checks take a shared lock when one already exists; a check
without an existing lock is a read-only snapshot and can become stale. Finish
editing the workflow files before applying an upgrade: ordinary editors do not
participate in that lock. The updater rechecks content before replacement and
atomically refuses to overwrite a newly appearing file, but it cannot provide a
transaction with an unrelated editor writing the same file at that instant.

Workflow and tracking paths must use real directories and regular files.
Symlinks are refused. An invalid tracking manifest produces an error rather than
being silently replaced; preserve it while correcting the file. Updates create
private tracking directories and files with restrictive permissions.
