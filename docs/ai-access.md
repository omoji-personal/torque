# De-identified mode

`ai_access` is a workspace setting for a firm with an AI-use policy. It lets an
AI coding session run inside a Torque workspace while keeping it away from
client orgs and client context. It is a best-effort guard for an assistant
following normal tool use, not a sandbox, and has two values:

- `full` (default): unrestricted, same as today.
- `build-only`: blocks org access, client context, and self-disabling.

## What build-only blocks

- Any `sf`/`sfdx` invocation carrying an org flag (`-o`, `--target-org`,
  `--from-org`, `-u`, `--targetusername`, `--target-dev-hub`, `-v`) anywhere on
  the line, even behind a wrapper, an env-var prefix, `npx`, or a subshell.
  Without one, only local generators, `--version`, `--help`, bare `version`,
  `help`, or `plugins` are allowed.
- Any `torque` subcommand, including via `python -m torque`, other than
  `demo`, `workflows`, `doctor`, `--version`, or `--help`.
- Reading, writing, or editing anything resolving into `clients/`: absolute,
  relative, `..`, or symlinked. A `Grep`/`Glob` with no `path`, or a pattern
  naming `clients`, counts too when the working directory is at or inside it.
- Any command or edit targeting `workspace.json` or `.claude/settings*.json`,
  via `sed`, redirection, `mv`, `rm`, `cp`, `tee`, or a script. The session
  cannot turn the mode off or remove the hook that enforces it.
- A missing/unreadable `workspace.json`, malformed hook input, or any other
  evaluation failure: fails closed, blocking the call.

## What it allows

Local generators, git, tests, editing non-client source such as `project/`
metadata, and the packaged conversational workflows.

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

The hook reads the tool-call JSON on stdin, exits 0 to allow, and exits 2 with
a reason on stderr to block.

## What it cannot stop

A script the assistant writes and runs that builds a command at run time, a
network tool reaching the org or a client system directly, or a host that
does not wire up this hook. Pair it with real access controls for anything
that must be enforced, not discouraged.
