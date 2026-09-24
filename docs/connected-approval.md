# Connected mode with per-write approval

Connected mode lets an AI session do real delivery work for one client: read that
client's orgs, run check-only deploys and tests, capture a before-state, and prepare
each change. Every org write still waits for the consultant: the session asks for an
approval of one exact command, the consultant reads it and grants it from their own
terminal, and the session may then run that command once. Each step is recorded in the
client's change record for a second reviewer.

It is opt-in. A workspace without `"ai_access": "connected"` behaves exactly as before
(`full` by default, or [build-only](ai-access.md)). Like build-only mode, it is a
best-effort gate on recognized tool calls in Claude Code, not an operating-system
sandbox. The limits are listed below.

## What the session can do

| Route | Examples | Decision |
|---|---|---|
| Local work | editors, `git`, `torque change`, `torque approval request/status/list/log` | allowed |
| Reads of the bound client's approved orgs | `sf data query -o acme-prod`, `sf project retrieve start`, Salesforce MCP query and describe tools | allowed when the consent covers the org and the data class (record data, debug logs) |
| Check-only | `sf project deploy validate`, `--dry-run`, `sf apex run test` | allowed and logged in `clients/<slug>/approvals/activity.jsonl` |
| Org writes | any other `sf`/`sfdx` command with an org flag, `sf api request` other than a plain GET, `torque deploy/data/org/recover`, `jsc` write verbs, Salesforce MCP tools that are not clearly reads | allowed once, by consuming a matching approval |
| Browser changes | clicks, typing and scripts in browser MCP servers; `torque browser`/`qa` with an org | allowed inside a granted browser window |
| Programs the gate cannot check | `python x.py`, `node`, `bash script.sh`, `npm run`, `pytest`, `curl` to a Salesforce host, `sf org login`, any program it does not recognize | the host asks the consultant when the session's permission mode is `default`, `acceptEdits` or `plan` (or the host sends none); refused in any other mode (`bypassPermissions`, `auto`, `dontAsk`, or one this version does not know) |
| Approval administration | `torque approval grant/deny`, `torque client consent record/sign-off/suspend`, `torque launch`, `torque workspace ai-access`, `torque approval permissions --write`, `sf alias set`, `sf config set`, desktop control (computer use) | refused |
| Out of scope | another client's folder or `--client`, an org not in the consent, an `sf` call without an explicit org | refused |

A session that was not started with `torque launch` has no client binding: every org,
client and browser route is refused.

## Setting it up

The workspace owner runs every step, in their own terminal (not through the AI session).
Steps marked "present" check that a person is at a real terminal outside the session.

1. Set the mode (present):
   `torque workspace ai-access connected --approval required --path W`
   (tier 2: add `--verify owner-uid --approver-uid UID`, see below). This also copies the
   rule file `production-approval.md` into `W/.claude/rules/`; leaving connected mode
   removes it.
2. Write the host permission rules (present):
   `torque approval permissions --workspace W --write`. It adds `ask` rules for every write
   route, interpreter and browser server, `deny` rules for approval administration and for
   edits to mode, consent, approval and Salesforce CLI files, and disables bypass mode. It
   adds no `allow` rule and removes allow rules for those routes. `--write` omitted prints
   the rules.
3. Wire the gate hook exactly as for build-only mode ([hook setup](ai-access.md#wiring-the-claude-code-hook)):
   the `-I` command, matcher `".*"`, `"timeout": 600`.
4. Record the client's written agreement (present):
   `torque client consent record --workspace W --client Acme --agreed-on 2026-09-30 --evidence agreement.pdf --data metadata --data records --org acme-sbx --org acme-prod --suspend-contact "Named person"`.
   Torque keeps a copy of the agreement with its hash and resolves each org live, recording
   its 18-character ID and whether it is production.
5. Record the second reviewer's sign-off (present):
   `torque client consent sign-off --workspace W --client Acme --reviewer "Reviewer name"`.
   Until then the consent is pending and the gate refuses org access for the client.
   `torque client consent suspend` stops connected work for the client at once.
6. Check readiness: `torque doctor --workspace W --client Acme --live`. It checks the hook,
   the permission rules, the approval tier, the consent, that each approved alias still
   resolves to its recorded org ID, and runs five synthetic calls through the hook (an org
   write, an unbound read, a script, an approval grant and a browser click), expecting deny,
   deny, ask, deny and deny. It exits 3 when anything is not ready.
7. Start the session (present): `torque launch --workspace W --client Acme [-- claude options]`.
   It binds the session to the client (`TORQUE_CLIENT`, which the hook inherits and the
   session cannot change) and starts `claude` in the workspace. Use one session per client.

## One write, start to finish (synthetic example)

The session prepares a Flow change for the synthetic client Acme and validates it:

```sh
sf project deploy validate -m Flow:Case_Escalation -o acme-prod        # allowed, logged
torque change create --workspace . --client Acme --title "Escalation" --outcome "Cases escalate" --org acme-prod
torque approval request --workspace . --client Acme --change chg-0123456789ab --org acme-prod \
  --capture-before-metadata Flow:Case_Escalation --validated-job 0Af000000000001AAA \
  -- sf project deploy start -m Flow:Case_Escalation -o acme-prod
```

The request retrieves the current Flow as an independent before-state, records a request
in the change and prints the request ID and the exact command. The session stops. The
consultant runs, in their own terminal:

```sh
torque approval grant req-8a1b2c3d4e5f --workspace W --client Acme
```

The grant re-derives everything from the request's command (the request file is not
trusted), shows the client, the org alias with its live 18-character ID and kind, the
exact command, the working folder, the components, the check-only job, the before-state
and its capture time, managed-package namespaces the command names, and a digest of the
files it deploys. The consultant types back a six-character code. The approval is valid for
15 minutes. The session then runs exactly the printed command, from the same folder. The
gate matches it, checks the approval's signature or owner, recomputes the file digest,
claims the approval once, logs `approval_consume` with the hook's `session_id` and
`tool_use_id`, and lets it run. Running it again is refused ("approval already used").

`torque approval log --workspace W --client Acme` lists every request, grant, denial and
use for the reviewer's sample, with any later deploy observation on the same org.

What an approval binds: the exact command text; the org alias and the org ID resolved at
grant, which the client's consent must still record for that alias when it is used; the
client; the change, whose record must still load when it is used; the working folder; the files the command deploys or loads
(the named files and folders, the manifest, and the project files matching each named
component; up to 2,000 files or 20 MB, above which the request is refused); a 15-minute
window (30 for a browser window) with 60 seconds of clock skew; single use. The gate
decides every other part of a call first and uses the approval only when the whole call is
allowed, so a call refused for another reason leaves its approval unused.

The grant screen shows every non-printable character (control, escape and bidirectional
formatting characters) as an escape such as `\x1b`, so what it shows is what will run.

For Torque's own routes (`torque deploy`, `data`, `org`, `recover`), the wrapper checks
again when it runs: it needs an approval the gate consumed for this exact command in the
last two minutes, and refuses when the alias now resolves to another org ID than the one
approved. A revert started by `torque recover` passes its approval to the wrapper it runs.
Scripts that write call `torque approval require --workspace W --client C --org A -- <command>`
first (exit 0 uses a matching approval, exit 3 refuses).

## Before-state for production

A production (or unknown) org needs an independent before-state or a written recovery
path before a grant, for every kind of approval. A browser window cannot have a
before-state (its actions are not known in advance), so a production browser window needs
`--manual-recovery`. For a command or MCP call, any of:

- `--before-state DIR`: an earlier retrieve or record export, copied into the change's
  evidence with a hash per file;
- `--capture-before-metadata Type:Name` or `--capture-before-record Object:Id`: read now,
  as its own recorded step (a record capture needs the `records` data class);
- `--manual-recovery TEXT`: at least 40 characters.

Each component the command names must appear in the before-state, or be declared new at
grant (`--new-component Type:Name`). A before-state file changed after capture blocks the
grant. The snapshot a Torque wrapper takes inside the approved write never counts.

## Browser windows

`torque approval request --browser --minutes 20 --purpose "Add Tier to the Case layout" --org acme-sbx ...`
asks for a window of up to 30 minutes. Once granted, browser actions (clicks, typing,
scripts, form input) are allowed for the bound client until it ends; reading and navigating
pages is always allowed. Clicks cannot be listed in advance, so the window is per client and
time, not per action. In a production org the request needs `--manual-recovery TEXT`.

## Two approval tiers

- **Tier 1, `hmac` (default).** The consultant and the session share one OS account.
  Approvals are signed with a key at `~/.config/torque/approval.key` (Windows:
  `%APPDATA%\torque\approval.key`), created at the first grant. The gate and the permission
  rules keep recognized tools away from the key, but **a script the session writes and runs
  can read the key and forge an approval**, and on macOS a script can drive a terminal
  (AppleScript, `tmux send-keys`) to answer the grant prompt. Tier 1 records intent and
  catches mistakes; it is not a boundary against a session that writes code to get around it.
- **Tier 2, `owner-uid` (recommended for stage 2).** The consultant grants from a separate
  OS account (the approver). The gate accepts an approval only when its file is owned by that
  account's uid and not writable by others, which the session's account cannot create. No
  key is involved. Setup: create the approver account; set the mode with
  `--verify owner-uid --approver-uid $(id -u APPROVER)`; install Torque and authenticate the
  Salesforce CLI to the client's orgs in the approver account (the grant resolves orgs
  itself); give the approver read access to the workspace and write access to
  `clients/<slug>/approvals/granted/`. On macOS use an inherited ACL, for example
  `chmod -R +a "APPROVER allow list,search,readattr,readextattr,readsecurity,read,file_inherit,directory_inherit" W`
  and `chmod +a "APPROVER allow add_file,write,file_inherit" W/clients/acme/approvals/granted`.
  On Linux use the equivalent `setfacl` entries. The grant is recorded in the change when the
  approval is used, if the approver cannot write the change record.

## What it stops

With the hook in force, on recognized routes:

- a known org write by Bash, a Torque route or an MCP tool without a granted approval;
- replay of an approval, its use for another command, payload, working folder, org or
  client, its use after its window, after its change record is gone, or after the consent
  stops naming the org ID it was granted for;
- the session granting its own approval through recognized routes (refused by the gate and
  the permission rules; the grant also needs a real terminal outside the session and a typed code);
- a write to an org outside the bound client's consent, and reads of another client's folder;
- org access for a client without active, signed-off consent;
- a production approval of any kind (browser windows included) without an independent
  before-state or a written recovery path;
- edits to the mode, consent and approval files, the approval key, and the Salesforce CLI's
  credential, alias and configuration folders and installation;
- changing an alias or the default org with `sf alias set` or `sf config set`;
- running programs the gate cannot check without the consultant's prompt (refused in every
  permission mode except `default`, `acceptEdits` and `plan`; doctor checks that bypass mode is
  disabled). A hook event with no permission mode is treated as prompting, so it asks;
- an `sf` read without an explicit org flag (it would read the default org, which no consent
  names);
- an alias remapped to another org between grant and a Torque-route run (wrapper check).

## What it cannot stop

- Code the session writes and runs (scripts, `python -c`, heredocs to interpreters, a git
  hook, a test runner's configuration): the host asks, and the consultant must read it first.
- In tier 1, a script reading the key and forging an approval, or driving a terminal to
  answer the grant.
- In tier 2, a script deleting a consume marker to run the same exact command again within
  its window (bounded by 15 minutes and the exact binding).
- A program the session places ahead of `sf` on `PATH` through a route the gate does not
  see, or a writable Salesforce CLI installation changed by a script; doctor warns about both.
- Commands built at run time, programs that run commands through their own options (`tar
  --to-command`, `rsync -e`, `zip -TT`, GNU `sed`'s `e`), and every route
  [build-only mode](ai-access.md#what-it-cannot-stop) lists as unparsed.
- Actions inside a granted browser window (per window, not per click), and actions a URL
  triggers during read-only navigation.
- An alias remapped before a raw `sf` write (doctor `--live` detects it at readiness time).
- Two tool calls running at the same time: a file edited while an approved deploy starts.
- The hook not running (missing, disabled, timed out, or a host without hooks).
- A consultant approving without reading; the screen and the typed code slow this down, and
  the reviewer's sample is the check.
- Data the session already read reaching the model provider; that is the consent question.
- Two machines writing to one org (the write lock is per machine).

The stage-2 account holding only approved clients' credentials remains the real boundary.

## Host facts verified

Checked on 2026-09-24 against Claude Code 2.1.281 (the hooks and permissions
documentation current on that date, plus a live session) and Salesforce CLI
2.150.6 (`sf commands --json`, 273 commands). Connected mode depends on each fact
below; recheck them when either tool changes its hook or command contract.

| Fact | Verified value | Used by |
|---|---|---|
| PreToolUse input keys | `session_id`, `transcript_path`, `cwd`, `permission_mode`, `hook_event_name`, `tool_name`, `tool_input`, `tool_use_id` | gate: consume record, bypass check |
| `permission_mode` values | `default`, `plan`, `acceptEdits`, `auto`, `dontAsk`, `bypassPermissions` | gate |
| Force a prompt | print `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": "..."}}` on stdout, exit 0 | gate `ask` |
| Block | exit 2; stderr is the reason. Exit 2 wins over any JSON and over allow rules | gate `deny` |
| Hook `ask` in `bypassPermissions` or `auto` mode | Not documented. Bypass mode skips permission prompts, so an `ask` cannot be relied on there | gate asks only in `default`, `acceptEdits` and `plan` (or with no mode); any other mode is refused |
| Hook decisions and rules | A matching deny or ask rule still applies when the hook returns `allow` or `ask` | permission generator |
| Rule order | deny, then ask, then allow; the first match decides | permission generator, doctor drift |
| Bash prefix rule | `Bash(sf apex run:*)` and `Bash(sf apex run *)` are equivalent; `:*` only at the end. Ask and deny rules also match inside compound commands and substitutions | permission generator |
| MCP rule | `mcp__server` (bare) or `mcp__server__*` matches every tool of a server; an `mcp__` rule with parentheses is skipped | permission generator |
| File rules | Only `Edit(path)` and `Read(path)` are consulted; a `Write(path)` rule is accepted but never used. `path` is relative to the current directory, `/path` to the settings file's project, `//path` absolute, `~/path` from home | permission generator (uses `Edit`, not `Write`) |
| Disable bypass | `permissions.disableBypassPermissionsMode: "disable"` in any settings file | permission generator, doctor |
| `defaultMode: bypassPermissions` | Takes effect only from user or managed settings (since 2.1.257), not project settings | doctor also checks the user settings file |
| Agent environment | Tool subprocesses have `CLAUDECODE=1` and `CLAUDE_CODE_ENTRYPOINT` set | presence check |
| Agent ancestry | The Bash tool's parent process is named `claude` | presence check |
| Hook environment | The hook inherits the Claude Code process environment; `CLAUDE_PROJECT_DIR` is set | client binding (`TORQUE_CLIENT`) |

`sf` commands that only read (from the 2.150.6 summaries) and that connected mode
allows for an approved org: `data query`, `data get record`, `data search`,
`data export tree|bulk|resume`, `data bulk results`, `data resume`,
`sobject describe|list`, `org display [user]`, `org list [limits|metadata|metadata-types|users|sobject record-counts]`,
`project retrieve start|preview`, `project deploy report|preview`,
`apex get log|test`, `apex list log`, `flow get test`, `logic get test`,
`package installed list`, `package install report`, `package uninstall report`,
`package version list`, `community list template`. `apex tail log` is not a read:
it turns on debug logging (a trace flag) in the org. `org auth show-access-token`,
`show-sfdx-auth-url` and `show-user-password` print credentials and are treated
as writes. Any command not on this list counts as a write.
