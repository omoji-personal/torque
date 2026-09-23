# De-identified mode

`ai_access` is a workspace setting for a firm with an AI-use policy: it lets an AI session run in a
Torque workspace while keeping it away from client orgs and client context. A best-effort guard,
not a sandbox, with two values:

- `full` (default, including when the key is absent): unrestricted.
- `build-only`: blocks org access, client context, and known ways to turn the mode off, heuristically.

## What build-only blocks

- Any `sf`/`sfdx` call carrying an org flag (`-o`, `--target-org`, `--from-org`, `-u`,
  `--targetusername`, `--target-dev-hub`, `-v`) anywhere, even behind a wrapper, env var, `npx`, or
  subshell. Without one: only local generators, `--version`, `--help`, `version`, `help`, `plugins`.
- Any `torque` subcommand, including via `python -m torque`, other than `demo`, `workflows`,
  `doctor`, `--version`, or `--help`.
- Reading, writing, editing, or recursively searching into `clients/` (absolute, relative, `..`,
  `~`/`$HOME`, symlinked), a `Grep`/`Glob` rooted at or above it or naming it, and `grep -r`, `rg`,
  `ag`, `ack`, `find`, `fd`, `tree`, `ls -R` (default target: cwd).
- A Bash command aimed at `workspace.json`, `.claude/settings*.json`, or the `.claude` directory as
  a whole (`Edit`/`Write`/`MultiEdit`/`NotebookEdit` are blocked only on `workspace.json` and
  `.claude/settings*.json`, not every file under `.claude/`), and a destructive command (`rm`,
  `mv`, `cp`, `truncate`, a redirect) using a glob at the workspace root.
- A real workspace marker (`clients/` plus `.torque/templates.json`, what `torque workspace init`
  writes) with no readable `workspace.json`, or any other evaluation failure: fails closed. `$HOME`
  itself, and above it, is never searched or treated as a workspace.

## What it allows

Local generators, git, tests, non-client source edits (`project/`), and the packaged
conversational workflows.

## Setting the mode

Only the workspace owner runs this, not the AI session. It writes `ai_access` and
`ai_access_changed_at` into `workspace.json`:

```sh
torque workspace ai-access build-only --path /path/to/workspace
torque workspace ai-access full --path /path/to/workspace
```

## Wiring the Claude Code hook

Put this in the workspace's own `.claude/settings.json`, never a user-level settings file: outside a
workspace there is no `workspace.json` to scope it, so it would run against every project on the machine.

```json
{"hooks": {"PreToolUse": [{"matcher": "Bash|Read|Edit|Write|MultiEdit|NotebookEdit|Grep|Glob",
  "hooks": [{"type": "command", "command": "python -m torque.gate"}]}]}}
```

It reads the tool-call JSON on stdin, exits 0 to allow, exits 2 to block.

## What it cannot stop

These are pattern matches on recognized tool calls, not a sandbox: a command built at run time, an
arbitrary script the assistant writes and runs, a network tool reaching the org or a client system
directly, or a host without this hook can all get through. Pair it with real access controls.
