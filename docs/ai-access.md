# De-identified mode

`ai_access` is a workspace setting for a firm with an AI-use policy. It lets an
AI session run inside a Torque workspace while keeping it away from client
orgs and client context. A best-effort guard, not a sandbox, with two values:

- `full` (default, including when the key is absent): unrestricted.
- `build-only`: blocks org access, client context, and known ways to turn
  the mode off, heuristically.

## What build-only blocks

- Any `sf`/`sfdx` invocation carrying an org flag (`-o`, `--target-org`,
  `--from-org`, `-u`, `--targetusername`, `--target-dev-hub`, `-v`) anywhere,
  even behind a wrapper, an env-var prefix, `npx`, or a subshell. Without
  one, only local generators, `--version`, `--help`, `version`, `help`, or
  `plugins` are allowed.
- Any `torque` subcommand, including via `python -m torque`, other than
  `demo`, `workflows`, `doctor`, `--version`, or `--help`.
- Reading, writing, editing, or recursively searching into `clients/`
  (absolute, relative, `..`, `~`/`$HOME`, symlinked); a `Grep`/`Glob` rooted
  at or above it or naming it; `grep -r`, `rg`, `ag`, `ack`, `find`, `fd`,
  `tree`, `ls -R` (default target: cwd).
- A command or edit aimed at `workspace.json`, `.claude/settings*.json`, or
  the `.claude` directory, and a destructive command (`rm`, `mv`, `cp`,
  `truncate`, a redirect) using a glob at the workspace root.
- A workspace marker (`clients/`/`.torque/`) with no readable `workspace.json`,
  or any other evaluation failure: fails closed.

## What it allows

Local generators, git, tests, non-client source edits (`project/`), and the
packaged conversational workflows.

## Setting the mode

Only the workspace owner runs this, not the AI session. It writes `ai_access`
and `ai_access_changed_at` into `workspace.json`:

```sh
torque workspace ai-access build-only --path /path/to/workspace
torque workspace ai-access full --path /path/to/workspace
```

## Wiring the Claude Code hook

```json
{"hooks": {"PreToolUse": [{"matcher": "Bash|Read|Edit|Write|MultiEdit|NotebookEdit|Grep|Glob",
  "hooks": [{"type": "command", "command": "python -m torque.gate"}]}]}}
```

It reads the tool-call JSON on stdin, exits 0 to allow, exits 2 to block.

## What it cannot stop

These are pattern matches on recognized tool calls, not a sandbox: a command
built at run time, an arbitrary script the assistant writes and runs, a
network tool reaching the org or a client system directly, or a host without
this hook can all get through. Pair it with real access controls.
