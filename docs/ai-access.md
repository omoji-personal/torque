# Build-only mode

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
- **One folder is guarded.** Only the governing workspace's `clients/` (with its copies in
  worktrees under `.claude/worktrees/`) and its `.claude/` configuration are protected. Mail, drive and chat connectors, other folders such as `private/`,
  and other workspaces are not, unless a tool argument names a path inside `clients/`.
- **The session runs as the user.** Any credential the operating-system user holds (a Salesforce
  CLI authorization, a token file, a browser session) is reachable by a script the session writes
  and runs. The guard does not inspect scripts. Run a build-only session in an account that holds
  no client-org authorizations.
- **Nothing is redacted.** Build-only mode redacts nothing: it blocks paths and commands, and
  removing client details (names, IDs, values) from text pasted into the session is the user's
  job. Redact alerts and notes before pasting them.

## What build-only blocks

- Any `sf`/`sfdx` call carrying an org flag (`-o`, `--target-org`, `--from-org`, `-u`,
  `--targetusername`, `--target-dev-hub`, `-v`) anywhere, even behind a wrapper, env var, `npx`, or
  subshell. Without one, only these pass: local generators (`project generate`, `lightning
  generate`, `apex generate`), `project convert`, `code-analyzer run` and `code-analyzer rules`
  (their roots must not reach `clients/`: `--workspace`/`--target` for code-analyzer,
  `--root-dir`/`--source-dir` for convert, and the current directory when none is given or when
  convert uses `--manifest`/`--metadata`), `--version`, `--help`, `version`, `help`, `plugins`. `code-analyzer`'s `-o` and `-v`
  short flags read as org flags; use `--output-file` and `--view`.
- Any `torque` subcommand, including via `python -m torque`, `python -mtorque`, or `py -m torque`,
  other than `demo`, `workflows`, `doctor` (without `--client`), `--version`, or `--help`. The
  `torque` command rejects abbreviated options, and the gate also blocks abbreviations of
  `--client` (`--clie`), which an older install would accept.
- Torque's other installed scripts (`jsc`, `jsc-qa`, `jsc-advisory`, `jsc-memory`,
  `jsc-loganalyzer`, `jsc-probes`, `jsc-browser-tests`, `meeting-processor`,
  `jsc-ai-prompt-regression`) and `python -m` on their modules (`jsc_*`, `meeting_processor`):
  anything other than `--help`, `-h`, or `--version`.
- MCP tools whose server or tool name indicates Salesforce access (a name containing `salesforce`,
  `sfdx`, `sf_`, `_sf`, `soql`, `sosl`, `sobject`, or `apex`, or a server or tool named `sf`), and
  any MCP call with a string argument (at any depth) that resolves into
  `clients/`, `workspace.json`, `.claude/`, or the installed Torque package. A tree-walking MCP
  tool (a name containing `tree`, `search`, `find`, `grep`, `glob`, or `walk`) rooted at or above
  `clients/` is blocked too. A `file:` URI is parsed as a URI: `file:///p`, `file://localhost/p`
  and `file:/p` all name `/p`, with `%` escapes decoded. An MCP tool that runs a command (a
  `command`, `cmd` or `script` argument at any depth) gets the Bash scan below on that string
  first, so a shell or process server cannot run what Bash may not.
- Reading, writing, editing, or recursively searching into `clients/`, through: absolute,
  relative, `..`, `~`, `$HOME`, `$PWD` and symlinked paths, and on Windows Git Bash drive paths
  (`/c/...`, `/cygdrive/c/...`); paths relative to a `cd`, `pushd` or `popd` earlier in the same
  command, also behind `builtin`, `command` or `time`, and `env -C DIR`/`--chdir` (after one whose
  target the gate cannot know, such as `cd -`, `cd ~-`, `popd`,
  `cd "$OLDPWD"` or `cd "$(git rev-parse --show-toplevel)"`, the rest of the command is checked
  from every directory seen, the workspace root, the folders between, and the root's parents);
  redirections (`<file`, `>file`, `2>file`, `&>file`), also glued to the word before them
  (`cat<file`); `--flag=path` and `NAME=path` values, and curl's `@file` and `<file` forms
  (`-d @file`, `-F name=@file`);
  ANSI-C and locale quoting (`$'\x63lients'`, `$"clients"`); globs (`c*/`, `*/acme`,
  `[c]lients`), expanded against the disk; brace expansion (`{clients,x}`); zsh glob groups and
  qualifiers (`c(l)ients`, `notes(.)`) and comma-less brace groups (`c{l..l}ients`), each read as
  a wildcard; `**` treated as a recursive search from its fixed prefix; `grep -r`, `rg`, `ag`,
  `ack`, `find`, `fd`, `tree`, `ls -R` (default target: the current directory; option values
  such as `-g '*.md'`, `-A 2` or `-A2` are not mistaken for the pattern or a path, a pattern given
  with `-e`, `-f`, `-eERROR`, `-rneERROR` or grep's `--regexp` and its abbreviations such as
  `--reg=` is not mistaken for a path, and modes with no pattern, rg's `--files` and ack's `-f`, read
  every word as a path); `git grep
  --untracked` or `--no-index`, `git diff --no-index` and `diff -r`, rooted at or above
  `clients/` (git's abbreviated forms, such as `--untr` or `--no-ind`, count too); `tar` (following
  its `-C DIR`, `-CDIR` and `--directory` changes in order, and blocking a directory known only at
  run time), and `zip`, `cp`, `scp` or `rsync` with a recursive flag, over a tree containing
  `clients/`; and a
  `Grep`/`Glob` rooted at or above `clients/` or naming it.
- Tools other than Bash that run a command string. Claude Code's `Monitor` runs in the Bash
  tool's shell, and a `PowerShell` tool (or any other tool with a `command`, `cmd` or `script`
  argument) gets the same scan, with PowerShell's backslashes read as path separators. That scan is best-effort
  for PowerShell syntax.
- Tools the gate does not recognise. Besides Bash, the file tools (`Read`, `Edit`, `Write`,
  `MultiEdit`, `NotebookEdit`, `NotebookRead`, `LS`, `LSP`), `Grep`, `Glob`, MCP tools and command
  tools, only these pass unchecked: `TodoWrite`, `TodoRead`, `TaskCreate`, `TaskUpdate`,
  `TaskList`, `TaskGet`, `Task`, `Agent`, `TaskOutput`, `TaskStop`, `BashOutput`, `KillShell`,
  `KillBash`, `WebSearch`, `WebFetch`, `ExitPlanMode`, `EnterPlanMode`, `AskUserQuestion`,
  `Skill`, `SlashCommand`, `ToolSearch`, `ListMcpResourcesTool`, `SendMessage` and
  `ExitWorktree`. `ReadMcpResourceTool` is checked like an MCP tool. Every other tool is blocked,
  including tools a host adds later.
- `LSP` calls other than `documentSymbol`, `hover` and `goToDefinition`. The others
  (`workspaceSymbol`, `findReferences`, `goToImplementation`, the call hierarchy, and any
  operation the gate does not know) answer from the language server's whole index, which is
  rooted at the workspace and covers `clients/`. The three allowed operations need a `filePath`;
  a `file:` URI is read as its path (host dropped, `%` escapes decoded), and every string
  argument is checked like an MCP tool's, so none may name `clients/`, `.claude/` or the Torque
  installation.
- `EnterWorktree` into anything but a worktree under `.claude/worktrees/`, or into a worktree's
  `clients/`. With a `name` (or no arguments) Claude Code creates a new worktree there, a copy of
  the tracked tree plus the gitignored files `.worktreeinclude` names. That is blocked when
  `git ls-files` finds tracked files in `clients/`, when `.worktreeinclude` mentions `clients` or
  matches a file in it, or when git cannot answer (no repository, git missing).
- Worktree copies. Every folder under `.claude/worktrees/` is checked as a workspace of its own,
  so its `clients/` is guarded like the workspace's: `Read`, `Grep`, `Glob`, `LSP`, MCP path
  arguments and Bash commands that reach `.claude/worktrees/<name>/clients/` are blocked.
- A Bash command aimed at `workspace.json`, `.claude/settings*.json`, `.worktreeinclude`, or the
  `.claude` directory (`Edit`/`Write`/`MultiEdit`/`NotebookEdit` are blocked only on
  `workspace.json`, `.claude/settings*.json` and `.worktreeinclude`), and a destructive command
  (`rm`, `mv`, `cp`, `truncate`, a redirection) using a glob at the workspace root.
- `git clean` without `-n`/`--dry-run`, and `git stash` with `-u`, `--include-untracked`, `-a` or
  `--all`, run at or above `clients/`, `.claude/`, the installed Torque package or the hook's
  Python environment. Either would delete those files or copy them into a stash. `git clean`
  works from the current directory (or `-C`, `--work-tree`, `GIT_WORK_TREE`) down, narrowed by
  its pathspecs; a stash covers its whole repository unless pathspecs narrow it. Reading a
  stash's untracked files back (`git stash show -u`, `--only-untracked`, or its third parent,
  `stash^3`, `stash@{0}^3`) is blocked in the same places. A magic pathspec (`:/`) and a
  repository git cannot identify count as reaching them.
- Changing Torque itself: `Write`/`Edit`/`MultiEdit`/`NotebookEdit` into the installed `torque`
  package directory, any Bash command naming that directory or Torque's install metadata
  (`torque_salesforce-*.dist-info`, `__editable__*torque*`), and `pip`, `python -m pip`, `uv` or
  `pipx` installing, upgrading, reinstalling or uninstalling a package whose name contains
  `torque` (plus `pipx *-all`). Reading the package stays allowed.
- Replacing the gate at the hook's Python startup. The documented hook runs Python with `-I`
  (isolated mode), so the working directory, `PYTHONPATH` and the user site directory are not
  on the import path, and a `torque` folder in the workspace is never imported. As a second
  layer, the file tools, MCP path arguments and Bash commands that write files (`cp`, `mv`,
  `tee`, `mkdir`, `ln`, a redirection and similar) are blocked from creating a `torque/` folder
  or anything in one, `torque.py`, a `.pth` file, `sitecustomize.py` or `usercustomize.py`
  anywhere in the workspace. Any path inside the hook interpreter's site-packages or its
  virtual environment's `pyvenv.cfg` is blocked outright, and writes to the interpreter binary
  and its virtual environment's `bin`/`Scripts` folder are blocked (running them stays
  allowed). Deleting, moving, locking (`chmod`, `chown`) or recreating (`python -m venv`,
  `virtualenv`, `uv venv`) the package, the interpreter's site-packages, scripts folder or binary,
  or any folder containing them (`rm -rf .venv`) is blocked too.

## Which workspace governs

The gate walks up to (not including) the user's home directory from three places: the tool call's
current directory, the session's project directory (`CLAUDE_PROJECT_DIR`, which Claude Code sets for
hooks), and every path the call names. So leaving the workspace with `cd ..` does not end the
session's gating, and a path inside a build-only workspace is checked from any directory. Every
folder with a `workspace.json`, or with a workspace marker (`clients/` plus `.torque/templates.json`)
and no readable `workspace.json`, counts. If any of them is build-only, the call is checked against
each build-only one. A nested `workspace.json` (even `{}` or `"full"`) cannot downgrade a build-only
workspace above it.

`ai_access` resolves as: key absent means `full`; exactly `"full"` means full; anything else
(`null`, `""`, a typo, a different case, a non-string) means build-only. An unreadable or malformed
`workspace.json`, a marker without one, or any evaluation error also means build-only.

## What it allows

Local generators, `sf project convert`, `sf code-analyzer` scoped away from `clients/`, git
(including plain `git grep`, which reads tracked files only, `git clean -n`, and `git stash`
without untracked files), tests, non-client source edits
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
{"hooks": {"PreToolUse": [{"matcher": ".*",
  "hooks": [{"type": "command", "command": "\"/path/to/venv/bin/python\" -I -c \"import os,sys;sys.excepthook=lambda t,e,b:(print('Build-only mode: the gate could not load ('+t.__name__+': '+str(e)+'); blocking to fail closed.',file=sys.stderr,flush=True),os._exit(2));from torque.gate import main;sys.exit(main())\""}]}]}}
```

The hook reads the tool-call JSON on stdin and exits 0 to allow or 2 to block. Claude Code treats
any other exit code as a non-blocking error and lets the tool run. The `sys.excepthook` wrapper
turns a failed `import torque.gate` (Torque missing from that interpreter, a broken install) into
exit 2, so that case blocks instead of silently passing. `-I` (isolated mode) keeps the
working directory, `PYTHONPATH` and the user site directory off the import path: without it,
Python imports a `torque` folder from the hook's working directory (the workspace) before the
installed one, and a single file write would replace the gate. The `.*` matcher sends every
tool call to the gate, which is what lets it scan `Monitor` and block tools it does not
recognise. The older form, `python -m torque.gate`, still runs but has neither protection: it
exits 1 when Torque cannot be imported, which lets every call through, and it imports from the
working directory.

The wrapper cannot help when the interpreter itself is missing (the shell exits 127, which is also
non-blocking) or when the session is started outside the workspace folder (the workspace's
`.claude/settings.json` is not loaded at all). Check both with doctor:

```sh
torque doctor --workspace /path/to/workspace
```

In build-only mode doctor runs the configured hook once on a synthetic `clients/` Read (on
Windows through Git Bash when installed, as Claude Code does) and reports
`AI access: build-only (hook command blocked a standalone probe; ...)`, or `HOOK NOT IN FORCE`
with the fix. It exits 3 when the hook is missing, did not block, could not load the gate, runs
without `-I`, has matchers that (read as regular expressions over tool names) miss any tool, or is
switched off by `disableAllHooks` in the workspace, user or managed settings (or by the managed
`allowManagedHooksOnly`). The probe runs the hook command directly; it shows the command blocks,
not that the running host calls it. Confirm that once in a real session. Run it after setup, after every Torque or Python update, and
before each monthly review.

## What it cannot stop

It is pattern matching on recognized tool calls, not a sandbox. Not covered:

- A command built at run time (a path from a variable set outside the command, from command
  output such as `$(... | base64 -d)`, or from `eval`), the code of
  `python -c`, any script file the assistant writes and runs, and a heredoc fed to an interpreter.
- A network tool (`curl`, a language HTTP client) reaching an org or a client system directly
  with credentials the user holds.
- MCP tools reaching client data that is not a path under `clients/`: mail, drive, chat, CRM or
  database connectors. Disable those connectors for a build-only session.
- A tool call the hook never sees: a matcher narrower than `.*` (doctor flags it), and a host
  without this hook. A host that does not set `CLAUDE_PROJECT_DIR` loses the session binding: a
  call from outside the workspace is then gated only when it names a path inside it.
- A workspace below the current directory that is neither the session's project nor named by a
  path in the call (for example `rg foo` run from the parent of another workspace).
- Breaking the hook's environment by a route the checks do not parse, such as a script, or `cp`
  over its interpreter from a path given at run time. When the interpreter is missing the hook
  exits 127, which Claude Code does not treat as a block.
- Replacing the gate by a route the write checks do not parse. A script or `python -c` code the
  assistant runs, `git checkout` or `git apply` of a tracked `torque/` folder, a download tool
  writing a file (`curl -o`), or an archive extracted into the workspace can still create a
  `torque/` folder, `sitecustomize.py` or a `.pth` file. With the documented `-I` hook none of
  those in the workspace is imported. A hook without `-I` (including `python -m torque.gate`)
  would import them; doctor flags such a hook. A script can also write into the hook
  interpreter's site-packages, its binary or a system site directory it loads, since the gate
  does not inspect scripts; that interpreter should live in a folder the session's account
  cannot write to.
- `pip install -r` of a requirements file, or `pip install .` from a Torque checkout, that
  replaces Torque without naming it on the command line.
- Copy or archive tools other than `tar`, `zip`, `cp`, `scp` and `rsync` with a recursive flag
  (for example `7z`, `ditto`, `robocopy`) run over the workspace.
- An MCP tool that runs code from an argument other than `command`, `cmd` or `script` (for
  example an `args` list or a `code` string). Only those three are scanned as commands; disable
  shell and process MCP servers in a build-only workspace.
- `SendMessage`. It passes unchecked. Within the session it reaches subagents and teammates whose
  own tool calls are gated, but a message to a peer session that is not gated can ask it to read
  `clients/` and reply. `ListAgents` is blocked, and the gate cannot tell a subagent from a peer.
  Do not run other Claude Code sessions with access to the workspace alongside a build-only one.
- What a language server returns for an allowed `LSP` call. Its index covers `clients/`, so
  `hover` or `goToDefinition` on a project file can still show a location or text from
  `clients/` when project code refers to it. Exclude `clients/` in the language server's own
  configuration where it has one.
- What the host does on `EnterWorktree` beyond the checks above: the gate assumes Claude Code
  creates worktrees under `.claude/worktrees/` and copies only the tracked tree and what
  `.worktreeinclude` names. A `WorktreeCreate` hook that copies more is not inspected.
- Git routes other than the ones named: an alias (`git -c alias.x=clean x`), a stash's untracked
  files read by object hash, `git checkout` or `git reset` of tracked files under `.claude/`
  from history, and a work tree set in an earlier command (`export GIT_WORK_TREE=..`).

It also over-blocks: a `Grep` whose pattern mentions `clients` (for example a custom object named
`Clients__c`) from the workspace root; an MCP string argument that is exactly `clients`, or `.`
for a tree-walking MCP tool; a glob at the workspace root that matches `clients/`, such as
`echo *`, `ls *`, `grep foo *`, `git add *` or `npx prettier --check '**/*.ts'` (these would list,
stage, read or rewrite client files; name the files or run them from `project/`); a regex
argument that also works as a glob matching a root entry (`.*` matches `.claude`);
`cd project; rg foo` and similar chains (after `;`, `||` or `|` the `cd` may have failed, so the
gate also checks the directory before it; use `cd project && rg foo`); a recursive search after
a `cd` the gate cannot resolve (`cd "$DIR" && rg foo` is checked from the workspace root too; use
a literal path); Bash reads under `.claude/` or of the installed Torque package; writing any
`torque/` folder, `torque.py`, `.pth` file or `sitecustomize.py` in the workspace; tools missing
from the recognised list above; a command that merely mentions a script name as an argument
(`rg jsc-qa`); nearly every Bash command run from inside a worktree under `.claude/worktrees/`
(its relative paths are under `.claude/`; use absolute paths to `project/` or leave the
worktree); `EnterWorktree` with a `name` in a workspace that is not a git repository; `git clean`
from the workspace root, even when `clients/` is ignored; and `LSP` on a path under `.claude/` or
in the Torque installation. Run those from `project/` or yourself.
