# Agent hosts

Torque runs under two agent hosts: Claude Code (`claude`) and Antigravity (`agy`, with Gemini
models). This page says what works under each one today, what was checked in a live session
and what only in tests, and where the two differ. `src/torque/hosts.py` is the registry: each
host's binary, session markers, playbook folder, hook file and launch options.

## Choosing the host

- `workspace.json` may name the host: `"host": "claude"` or `"host": "antigravity"` (`agy` and
  `gemini` are read as Antigravity). Without the key the host is Claude Code. The owner edits
  the file. No command sets the key yet. Any other value is an error in `torque launch` and
  `torque doctor`.
- `torque launch --host claude|antigravity` picks the host for one launch. Without it the
  workspace's host is started.
- `torque doctor` judges readiness by the host the workspace names.

## What works today

| Area | Claude Code | Antigravity |
|---|---|---|
| Workspace files | `init` and `upgrade` write rules, recipes, worker roles and skills under `.claude/`. | The same content under `.agents/` in Antigravity's format. A recipe is a skill. `/help`, `/context` and `/undo` are `/torque-help`, `/torque-context` and `/torque-undo`, and text that mentions them names the new ones. |
| Mode rule | `build-only.md` in a build-only workspace and `production-approval.md` in a connected one, in `.claude/rules/`. | The same two in `.agents/rules/`, with the `trigger: always_on` header. |
| Build-only gate | Hook in `.claude/settings.json`. | Hook in `.agents/hooks.json` (`torque.gate_antigravity`). The same gate decides. The answer format differs, see below. |
| Connected mode and launch | `torque launch` starts `claude`, bound to one client. | `torque launch --host antigravity` starts `agy`, bound to one client. Written and tested offline. Not run in a live session. |
| Approvals | Request, grant, single use. A record of how the approved call ended, when the after-call hooks are wired. | Request, grant and single use run through the same code. No record of how the call ended. |
| Delegated launch | Passes only the listed `claude` options. | Passes only `-p`, `--print`, `--model`, `--add-dir`, `--output-format` and `--print-timeout`. Not run in a live session. |
| Permission rules | `torque approval permissions` writes them to `.claude/settings.json`. | None. Torque writes no permission rules for Antigravity. |
| Doctor, build-only | Checks the hook and runs it once on a made-up client path. | The same for the Antigravity hook. |
| Doctor, connected | Checks the hook, the rules, consent and the probes. | Not checked. Doctor says so and reports not ready. |

## Mode rules

A build-only workspace gets `build-only.md` and a connected one gets `production-approval.md`,
for both hosts. `torque workspace ai-access` writes the rule of the new mode and removes the
rule of the old one. `torque workspace upgrade` adds a missing rule, updates an unchanged one
and keeps an edited one, as it does for other packaged files. `--check` shows this without
writing. Two exceptions:

- `.claude/rules/production-approval.md` is written and removed by `ai-access` only, as before.
  `upgrade` does not track it.
- A delegated `ai-access` step writes only the files listed for it. The Antigravity copy of
  the connected rule then arrives with the next `upgrade`.

The rule tells the agent what the mode means. The gate is what blocks a call.

## Doctor

- Host Claude Code, or no host named: as before, in both modes. In build-only the Claude Code
  hook must work. A missing Antigravity hook is only a suggestion. A registered Antigravity
  hook that fails is a fault.
- Host Antigravity, build-only: the Antigravity hook must be registered, block the probe, see
  every tool and run with `-I`. Findings about the Claude Code hook are advice only. They do
  not change readiness.
- Host Antigravity, connected: doctor runs its Claude Code checks and then reports not ready.
  It has no checks for a connected Antigravity session yet.

The probe runs the hook command by itself. It does not show that the host calls the hook.

## What was checked, and how

Torque's own hook has not yet been called by a live Antigravity session, and no live
session has loaded the files Torque writes under `.agents/`. What was seen live is how
Antigravity treats a hook and those file formats in general. Torque's hook was then run as
a command on that input: 35 made-up calls across build-only, full and connected workspaces.

Recorded earlier as seen with Antigravity CLI 1.3.1 on Windows (see `ai-access.md` and
`workspace-upgrades.md`). This page adds nothing to those observations:

- The format of rules, skills and worker roles in `.agents/`.
- The hook input, the answer format, and that a hook that crashes, times out or prints nothing
  blocks the call.
- The tool names and arguments the hook rewrites.

Checked only in tests, with no Antigravity session:

- Everything about connected mode under Antigravity: the launch, the host in the launch
  record, the binding, the call ID, approvals and the browser tools.
- The delegated launch list for `agy`. It is not known that `agy` reads each option as written.
- That Antigravity loads the two mode rules. They use the format of the other rules.
- The three hook fixes in this version (typed input without text, an MCP call without a
  server or tool name, and the scan of `browser_subagent`, `schedule` and `read_url_content`).
- Doctor's readiness by host.
- That an `agy` or `antigravity` process counts as an AI session on macOS and Linux. It is not
  known that a running session shows under either name.

The delegated launch and the process checks exist on macOS and Linux only, so their tests run
only there.

## Known differences

- **No "no opinion" answer.** Claude Code lets a hook stay silent. Antigravity does not. A
  call the gate lets through is answered `allow` only when it is a read inside the folders the
  session was started with. Every other such call is answered `ask`, so Antigravity's own
  permission flow decides.
- **The permission mode cannot be seen.** Under Claude Code the gate refuses a call it cannot
  check when prompts are skipped. Antigravity sends no permission mode, so the gate answers
  `ask` there too. A session started with `--dangerously-skip-permissions` then runs it. A
  delegated launch never passes that option. An owner launch can.
- **Unattended sessions.** Under `claude -p` an `ask` has no one to answer it, so the call is
  refused. It is not known what `agy -p` does with `ask`. A session launched with a launch
  binding is therefore answered `deny` where the gate would ask. A headless Antigravity
  session that Torque did not launch still gets `ask`.
- **No rules file for permissions.** Under Claude Code the generated permission rules are a
  second layer. Under Antigravity the hook is the only layer.
- **The process check is skipped on Windows, for both hosts.** On macOS and Linux a launch
  record binds only when the launched process is the hook's own process or one of its nearest
  ancestors, with the same start time. On Windows that check does not run.
- **Nothing marks an Antigravity session on Windows.** No environment variable is known that
  Antigravity sets for its commands, so none is listed. On macOS and Linux the process name is
  checked. On Windows the presence check rests on the terminal test and the typed code.
- **A launch record names its host.** A record written for one host does not bind under the
  other host's hook. The Antigravity hook states its host in `TORQUE_HOOK_HOST`. Claude Code's
  hook sets nothing, and unset means Claude Code. This is the hook's own statement. It is not
  proof: whoever starts a hook process chooses its environment.
- **The binding needs the right folder.** Under Antigravity a session stays bound only while
  its first workspace folder is the workspace the launch record names.
- **A launch binding names no host.** The account that claims it chooses the host.
- **Browser tools.** In build-only and connected mode Antigravity's own browser tools are
  refused, as a browser MCP server is under Claude Code. Its web fetch (`read_url_content`)
  is refused for an address on a Salesforce org host and passes for any other.
- **No record after the call.** The Antigravity hook handles the before-call event only.
- **The call ID** is the conversation ID and the step number. It is not known to be unique
  when one step makes several calls.
- **`torque launch` does not check the hook.** For either host, run `torque doctor` first. A
  session without its hook is not gated.
