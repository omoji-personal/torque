# De-identified mode

`ai_access` is a workspace setting for a firm with an AI-use policy. It lets an
AI coding session run inside a Torque workspace while keeping it away from
client orgs and client context. It has two values:

- `full` (default): unrestricted, same as today.
- `build-only`: blocks org access and client context; allows local build work.

## What build-only blocks

- Any `sf` or `sfdx` command other than local generators, `--version`, `--help`,
  `version`, or `plugins`. These can reach a live org.
- Any `torque` subcommand other than `demo`, `workflows`, `doctor`, `--version`,
  or `--help`. Others read client context or a configured org.
- Reading, writing, or editing anything under a client's `clients/` directory.
- Writing or editing `workspace.json`, including the `ai_access` field itself.

## What it allows

Local generators, git, tests, editing non-client source such as `project/`
metadata, and the packaged conversational workflows.

## Setting the mode

Only the workspace owner runs this, not the AI session (build-only mode
blocks the AI from running it):

```sh
torque workspace ai-access build-only --path /path/to/workspace
torque workspace ai-access full --path /path/to/workspace
```

This writes `ai_access` and `ai_access_changed_at` into the workspace's
`workspace.json` using Torque's atomic writer.

## Wiring the Claude Code hook

Add a `PreToolUse` hook so the gate runs before each tool call:

```json
{"hooks": {"PreToolUse": [{"matcher": "Bash|Read|Edit|Write|MultiEdit|NotebookEdit|Grep|Glob",
  "hooks": [{"type": "command", "command": "python -m torque.gate"}]}]}}
```

The hook reads the tool-call JSON on stdin, exits 0 to allow, and exits 2 with
a reason on stderr to block. It resolves the mode from the nearest
`workspace.json` above the session's working directory, defaulting to `full`
when none is found.

## Limits

This is a best-effort assistant guard, not a sandbox. It checks recognized
`Bash`, `Read`, `Edit`, `Write`, `MultiEdit`, `NotebookEdit`, `Grep`, and
`Glob` calls; it does not stop a determined session using another tool, a raw
shell escape, or a host without this hook wired up. Pair it with your firm's
actual access controls for anything that needs to be enforced, not just
discouraged.
