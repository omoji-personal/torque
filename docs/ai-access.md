# De-identified mode

`ai_access` is a workspace setting for a firm with an AI-use policy: it lets an AI session run in a
Torque workspace while keeping it away from client orgs and client context. It is a best-effort
guard on recognized tool calls, not a sandbox. It has two values:

- `full` (default when the key is absent): the guard is off and every tool call passes.
- `build-only`: blocks org access, client context, and known ways to turn the mode off.

## Current limits, stated plainly

- **No org allowlist.** Build-only blocks every Salesforce org, including a firm-owned developer
  org with synthetic data. The session can draft metadata, tests and a retest plan locally; a
  person runs every deploy, test run and query against any org, outside the AI session.
- **No metadata-only mode.** There are only two modes. Switching to `full` removes the guard
  entirely: the session can then reach any org the user's Salesforce CLI is authorized for, read
  and write records, and read `clients/`. A scoped middle mode does not exist yet.
- **The setting is not authenticated.** Whoever owns the workspace folder changes it.
  `ai_access_changed_at` records when, in a file that owner can edit. Leadership approval is a
  process around the setting, not something Torque enforces.
- **One folder is guarded.** Only the governing workspace's `clients/` (and its `.claude/`
  configuration) is protected. Mail, drive and chat connectors, other folders such as `private/`,
  and other workspaces are not, unless a tool argument names a path inside `clients/`.
- **The session runs as the user.** Any credential the operating-system user holds (a Salesforce
  CLI authorization, a token file, a browser session) is reachable by a script the session writes
  and runs. The guard does not inspect scripts. Run a build-only session in an account that holds
  no client-org authorizations.
- **Nothing is de-identified.** The mode blocks paths and commands; it does not remove names, IDs
  or values from text pasted into the session. Redact alerts and notes before pasting them.

## What build-only blocks

- Any `sf`/`sfdx` call carrying an org flag (`-o`, `--target-org`, `--from-org`, `-u`,
  `--targetusername`, `--target-dev-hub`, `-v`) anywhere, even behind a wrapper, env var, `npx`, or
  subshell. Without one, only these pass: local generators (`project generate`, `lightning
  generate`, `apex generate`), `project convert`, `code-analyzer run` and `code-analyzer rules`
  (their `--workspace`/`--target` roots, default the current directory, must not reach
  `clients/`), `--version`, `--help`, `version`, `help`, `plugins`. `code-analyzer`'s `-o` and `-v`
  short flags read as org flags; use `--output-file` and `--view`.
- Any `torque` subcommand, including via `python -m torque`, `python -mtorque`, or `py -m torque`,
  other than `demo`, `workflows`, `doctor` (without `--client`), `--version`, or `--help`.
- Torque's other installed scripts (`jsc`, `jsc-qa`, `jsc-advisory`, `jsc-memory`,
  `jsc-loganalyzer`, `jsc-probes`, `jsc-browser-tests`, `meeting-processor`,
  `jsc-ai-prompt-regression`) and `python -m` on their modules (`jsc_*`, `meeting_processor`):
  anything other than `--help`, `-h`, or `--version`.
- MCP tools whose server or tool name indicates Salesforce access (a name containing `salesforce`,
  `sfdx`, `sf_`, `_sf`, `soql`, `sosl`, `sobject`, or `apex`, or a server or tool named `sf`), and
  any MCP call with a string argument (at any depth, `file://` URIs included) that resolves into
  `clients/`, `workspace.json`, `.claude/`, or the installed Torque package. A tree-walking MCP
  tool (a name containing `tree`, `search`, `find`, `grep`, `glob`, or `walk`) rooted at or above
  `clients/` is blocked too.
- Reading, writing, editing, or recursively searching into `clients/`, through: absolute,
  relative, `..`, `~`, `$HOME`, `$PWD` and symlinked paths; paths relative to a `cd` or `pushd`
  earlier in the same command; redirections (`<file`, `>file`, `2>file`, `&>file`); `--flag=path`
  and `NAME=path` values; globs (`c*/`, `*/acme`, `[c]lients`), expanded against the disk;
  brace expansion (`{clients,x}`); `**` treated as a recursive search from its fixed prefix;
  `grep -r`, `rg`, `ag`, `ack`, `find`, `fd`, `tree`, `ls -R` (default target: the current
  directory); `git grep --untracked` or `--no-index` rooted at or above `clients/`; `tar`, and `zip`, `cp`,
  `scp` or `rsync` with a recursive flag, over a tree containing `clients/`; and a
  `Grep`/`Glob` rooted at or above `clients/` or naming it.
- A Bash command aimed at `workspace.json`, `.claude/settings*.json`, or the `.claude` directory
  (`Edit`/`Write`/`MultiEdit`/`NotebookEdit` are blocked only on `workspace.json` and
  `.claude/settings*.json`), and a destructive command (`rm`, `mv`, `cp`, `truncate`, a
  redirection) using a glob at the workspace root.
- Changing Torque itself: `Write`/`Edit`/`MultiEdit`/`NotebookEdit` into the installed `torque`
  package directory, any Bash command naming that directory or Torque's install metadata
  (`torque_salesforce-*.dist-info`, `__editable__*torque*`), and `pip`, `python -m pip`, `uv` or
  `pipx` installing, upgrading, reinstalling or uninstalling a package whose name contains
  `torque` (plus `pipx *-all`). Reading the package stays allowed.

## Which workspace governs

The gate walks up from the session's directory to (not including) the user's home directory. Every
folder with a `workspace.json`, or with a workspace marker (`clients/` plus `.torque/templates.json`)
and no readable `workspace.json`, counts. If any of them is build-only, the call is checked against
each build-only one. A nested `workspace.json` (even `{}` or `"full"`) cannot downgrade a build-only
workspace above it.

`ai_access` resolves as: key absent means `full`; exactly `"full"` means full; anything else
(`null`, `""`, a typo, a different case, a non-string) means build-only. An unreadable or malformed
`workspace.json`, a marker without one, or any evaluation error also means build-only.

## What it allows

Local generators, `sf project convert`, `sf code-analyzer` scoped away from `clients/`, git
(including plain `git grep`, which reads tracked files only), tests, non-client source edits
(`project/`), and the packaged conversational workflows.

## Setting the mode

Only the workspace owner runs this, not the AI session. It writes `ai_access` and
`ai_access_changed_at` into `workspace.json`:

```sh
torque workspace ai-access build-only --path /path/to/workspace
torque workspace ai-access full --path /path/to/workspace
```

## Wiring the Claude Code hook

Put this in the workspace's own `.claude/settings.json`, never a user-level settings file: outside a
workspace there is no `workspace.json` to scope it, so it would run against every project on the
machine. Replace `/path/to/venv/bin/python` with the absolute path of an interpreter that has Torque
installed (forward slashes on Windows). `torque doctor --workspace .` prints this exact command for
the interpreter it runs under, as `ai_access.hook.recommended_command` in `--json` output.

```json
{"hooks": {"PreToolUse": [{"matcher": "Bash|Read|Edit|Write|MultiEdit|NotebookEdit|Grep|Glob|mcp__.*",
  "hooks": [{"type": "command", "command": "\"/path/to/venv/bin/python\" -c \"import os,sys;sys.excepthook=lambda t,e,b:(print('De-identified mode: the gate could not load ('+t.__name__+': '+str(e)+'); blocking to fail closed.',file=sys.stderr,flush=True),os._exit(2));from torque.gate import main;sys.exit(main())\""}]}]}}
```

The hook reads the tool-call JSON on stdin and exits 0 to allow or 2 to block. Claude Code treats
any other exit code as a non-blocking error and lets the tool run. The `sys.excepthook` wrapper
turns a failed `import torque.gate` (Torque missing from that interpreter, a broken install) into
exit 2, so that case blocks instead of silently passing. The older form, `python -m torque.gate`,
still works but exits 1 when Torque cannot be imported, which lets every call through.

The wrapper cannot help when the interpreter itself is missing (the shell exits 127, which is also
non-blocking) or when the session is started outside the workspace folder (the workspace's
`.claude/settings.json` is not loaded at all). Check both with doctor:

```sh
torque doctor --workspace /path/to/workspace
```

In build-only mode doctor runs the configured hook once on a synthetic `clients/` Read and reports
`AI access: build-only (hook verified)`, or `HOOK NOT IN FORCE` with the fix, and exits 3 when the
hook is missing or did not block. Run it after setup, after every Torque or Python update, and
before each monthly review.

## What it cannot stop

It is pattern matching on recognized tool calls, not a sandbox. Not covered:

- A command built at run time (a path assembled from variables, `eval`, `cd "$DIR"`), the code of
  `python -c`, any script file the assistant writes and runs, and a heredoc fed to an interpreter.
- A network tool (`curl`, a language HTTP client) reaching an org or a client system directly
  with credentials the user holds.
- MCP tools reaching client data that is not a path under `clients/`: mail, drive, chat, CRM or
  database connectors. Disable those connectors for a build-only session.
- Any tool outside the hook matcher, including tools a host adds later, and a host without this
  hook.
- `pip install -r` of a requirements file, or `pip install .` from a Torque checkout, that
  replaces Torque without naming it on the command line.
- Copy or archive tools other than `tar`, `zip`, `cp`, `scp` and `rsync` with a recursive flag
  (for example `7z`, `ditto`, `robocopy`) run over the workspace.

It also over-blocks: a `Grep` whose pattern mentions `clients` (for example a custom object named
`Clients__c`) from the workspace root; an MCP string argument that is exactly `clients`; `echo *`
or `ls *` at the workspace root (the glob matches `clients/`); `cd project; rg foo` and
similar chains (after `;`, `||` or `|` the `cd` may have failed, so the gate also checks the
directory before it; use `cd project && rg foo`); Bash
reads under `.claude/` or of the installed Torque package; and a command that merely mentions a
script name as an argument (`rg jsc-qa`). Run those from `project/` or yourself.
