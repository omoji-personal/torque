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
| Reads of the bound client's approved orgs | `sf data query -o acme-prod`, `sf project retrieve start`, Salesforce MCP query, get and describe tools, `sf api request rest` GET | allowed when the consent covers the org and the data class. Record data (queries, searches, exports, record gets, REST record and query paths, the same reads in legacy `sfdx` and MCP form) needs `records`; Apex logs need `debug_logs`; a REST path Torque cannot place counts as record data |
| Check-only | `sf project deploy validate`, `--dry-run`, `sf apex run test` | allowed and logged in `clients/<slug>/approvals/activity.jsonl` |
| Org writes | any other `sf`/`sfdx` command with an org flag, `sf api request` other than a plain GET, `torque deploy/data/org/recover`, `jsc` write verbs, Salesforce MCP tools that are not clearly reads | allowed once, by consuming a matching approval |
| Browser changes | clicks, typing and scripts in browser MCP servers; `torque browser`/`qa` with an org | allowed inside a granted browser window for the org the browser is in (see below) |
| Programs the gate cannot check | `python x.py`, `node`, `bash script.sh`, `sh -c '...'`, `npm run`, `pytest`, `curl` to a Salesforce host, `sf org login`, any program it does not recognize | the host asks the consultant when the session's permission mode is `default`, `acceptEdits` or `plan` (or the host sends none) |
| Approval administration | `torque approval grant/deny`, `torque client consent record/sign-off/suspend`, `torque launch`, `torque workspace ai-access`, `torque approval permissions --write`, `sf alias set`, `sf config set`, desktop control (computer use) | refused (`torque client consent show`, which only displays the bound client's own record, stays allowed) |
| Out of scope | another client's folder or `--client`, `torque client list`, an org not in the consent (whatever the route), an `sf` call without an explicit org | refused |
| Prompts skipped | any org write, browser change or unchecked program while the session's permission mode is not `default`, `acceptEdits` or `plan` (`bypassPermissions`, `auto`, `dontAsk`, or a mode this version does not know) | refused, even with an approval, which stays unused |

A session that was not started with `torque launch` has no client binding: every org,
client and browser route is refused.

## Setting it up

The workspace owner runs every step, in their own terminal (not through the AI session).
Steps marked "present" check that a person is at a real terminal outside the session.

Each "present" step also prints a six-character code the owner types back.

1. Set the mode (present):
   `torque workspace ai-access connected --approval required --path W`
   (tier 2: add `--verify owner-uid --approver-uid UID`, see below; not available on
   Windows). This also copies the
   rule file `production-approval.md` into `W/.claude/rules/`; leaving connected mode
   removes it.
2. Write the host permission rules (present):
   `torque approval permissions --workspace W --write`. It adds `ask` rules for every write
   route, interpreter and browser server, `deny` rules for approval administration and for
   edits to mode, consent, approval and Salesforce CLI files, and disables bypass mode. It
   adds no `allow` rule and removes allow rules for those routes. `--write` omitted prints
   the rules.
3. Wire the gate hook exactly as for build-only mode ([hook setup](ai-access.md#wiring-the-claude-code-hook)):
   the fail-closed `-I` command, matcher `".*"`, `"timeout": 600`.
4. Record the client's written agreement (present):
   `torque client consent record --workspace W --client Acme --agreed-on 2026-09-30 --evidence agreement.pdf --data metadata --data records --org acme-sbx --org acme-prod --suspend-contact "Named person"`.
   Torque keeps a copy of the agreement with its hash and resolves each org live, recording
   its 18-character ID, whether it is production, and its My Domain address (browser
   windows are bound to it; re-record a consent made before 2.0.0a15's review fixes).
5. Record the second reviewer's sign-off (present):
   `torque client consent sign-off --workspace W --client Acme --reviewer "Reviewer name"`.
   Until then the consent is pending and the gate refuses org access for the client.
   `torque client consent suspend` stops connected work for the client at once.
6. Check readiness: `torque doctor --workspace W --client Acme --live`. It checks the hook
   (fail-closed form, `-I`, matcher, timeout), the effective permission rules across the
   user, project and local settings files, the approval tier, the consent, that each
   approved alias still resolves to its recorded org ID, and runs synthetic calls through the
   hook: six unbound (an org write, a read, a check-only deploy, a script, an approval grant
   and a browser click: deny, deny, deny, ask, deny, deny; the check-only probe is refused
   before anything is logged) and, with `--client`, seven bound to that client (a
   read of an approved org: allow; an unapproved write, an org outside the consent, the
   default org and another client: deny; a script: ask; a script with prompts skipped:
   deny). The hook only decides; nothing it allows is run. It exits 3 when anything is not
   ready.
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
exact command, the working folder, the components, the check-only job with its result read
live (`sf project deploy report`), the before-state with how and when it was captured and
from which org, managed-package namespaces the command names, and a digest of the files it
deploys. With `--audit-trail FILE` (Setup Audit Trail rows from `sf data query --json`), it
warns about any Setup change to a listed component after the capture. The consultant types back a six-character code. The approval is valid for
15 minutes. The session then runs exactly the printed command, from the same folder. The
gate matches it, checks the approval's signature or owner, recomputes the file digest,
claims the approval once, logs `approval_consume` with the hook's `session_id` and
`tool_use_id`, and lets it run. Running it again is refused ("approval already used").

`torque approval log --workspace W --client Acme` lists every request, grant, denial and
use for the reviewer's sample, with the deploy observations recorded later on the same org:
an observation whose job ID is the approval's validated job is marked linked, any other is
listed as unlinked.

What an approval binds: the exact command text (`command_sha256`); the org alias and the
org ID resolved at grant, which the client's consent must still record for that alias, with
the consent still active and signed off, when it is used; the client; the change, whose
record must still load when it is used; the working folder; and the files the command
deploys or loads. Those are every file and folder any payload flag names (every value of a
multi-value flag, attached or separate, legacy `sfdx` spellings and comma lists included),
the data files a `sf data import tree` plan names, the manifest, the project files each
named component comes from (a component kept in a shared file, such as a custom label,
workflow rule or sharing rule, binds that whole file; an object's child binds its object
folder), every package folder for a deploy with no selector, and for an MCP call every file
or folder its input names. A named file that does not exist, a link, or a named component
with no file in the project's package folders is refused rather than hashed as empty. A 15-minute window (30 for a browser
window) with 60 seconds of clock skew; single use. The gate decides every other part of a
call first and uses the approval only when the whole call is allowed, so a call refused for
another reason leaves its approval unused.

Above 2,000 files or 20 MB the gate cannot check the files in its time budget. A raw `sf`
command that large is refused at request; the same deploy through `torque deploy` (or
`data`, `org`) is accepted, and its wrapper checks the full file digest itself before it
runs, with no time limit.

Every use is recorded before it is allowed: if the `approval_consume` event or the activity
log line cannot be written, the call is refused and the approval stays unused. Check-only
calls and browser actions that cannot be logged are refused too. Events carry the command,
`command_sha256`, the file digest, the org alias, ID and kind, the approver, the
before-state or recovery path, the validated job, the window and the hook's `session_id` and
`tool_use_id`. A manual recovery path is also recorded as a `decision` event.

The grant screen shows every non-printable character (control, escape and bidirectional
formatting characters) as an escape such as `\x1b`, so what it shows is what will run.

For Torque's own routes (`torque deploy`, `data`, `org`, `recover`), the wrapper checks
again when it runs. It finds connected mode itself (the selected workspace's
`workspace.json`, or a connected workspace at or above the working folder), and refuses
when it cannot tell: a configuration that cannot be read, or a selected client or a
workspace marker with no configuration. It needs an approval the gate consumed for this
exact command in the last two minutes, run from the approved working folder, verifies that
approval again (signature or owner, window, consent, change) and its files, and refuses
when the alias now resolves to another org ID than the one approved. The claim is atomic:
two runs racing for one approval get it once. If the wrapper cannot resolve the org at all,
nothing runs and the approval is returned so the same command can be run again inside its
window (at most three times, each logged). A revert started by `torque recover` names the
one wrapper command it starts; that command runs once under the parent's approval, and if
it cannot resolve the org, the parent's approval is returned the same way.
Scripts that write call `torque approval require --workspace W --client C --org A -- <command>`
first (exit 0 uses a matching approval, exit 3 refuses).

## Before-state for production

A production (or unknown) org needs an independent before-state or a written recovery
path before a grant, for every kind of approval. A browser window cannot have a
before-state (its actions are not known in advance), so a production browser window needs
`--manual-recovery`. For a command or MCP call, any of:

- `--before-state PATH`: an earlier retrieve (a folder) or a record export, copied into the
  change's evidence with a hash per file. Each exported record is also stored as
  `records/Object__Id.json`, the form the grant checks a record write against: a JSON export
  (`sf data query --json`, `sf data export tree`) names its objects; a CSV export needs
  `--before-state-object OBJECT`. Its org is not verified, and the grant screen says so;
- `--capture-before --metadata Type:Name` or `--capture-before --record Object:Id`
  (also `--capture-before-metadata` / `--capture-before-record`): read now, as its own
  recorded step, after the org is checked live against the consent; the capture records the
  org ID it read from (a record capture needs the `records` data class);
- `--manual-recovery TEXT`: at least 40 characters.

The grant checks a before-state against what the write changes: the components a deploy
names (`--metadata`, a manifest, every file under `--source-dir`, and every component a
pre- or post-destructive manifest deletes), or the record a single-record update or delete
names. Each must have its content in the before-state (the definition file itself: the Apex
source, not only its `-meta.xml`; an object's own `.object-meta.xml`, not only one of its
fields; a component bundle's definition file), or be declared new at grant
(`--new-component Type:Name`). A write whose changes Torque cannot list (anonymous Apex,
bulk loads, a deploy with no selector) needs a manual recovery path instead. A before-state
captured from another org, captured after the request, or changed since capture blocks the
grant. The snapshot a Torque wrapper takes inside the approved write never counts.

## Browser windows

`torque approval request --browser --minutes 20 --purpose "Add Tier to the Case layout" --org acme-sbx ...`
asks for a window of up to 30 minutes for one org. Clicks cannot be listed in advance, so
the window is per org and time, not per action. In a production org the request needs
`--manual-recovery TEXT`.

A browser change is bound to the exact tab it acts in. The gate keeps, for this session,
the Salesforce orgs each tab (browser server and tab ID) was sent to through the browser
tools: a URL in a browser tool's input is matched against the My Domain address the consent
recorded for each approved org (production, sandbox, developer and Visualforce hosts).
Navigating to a Salesforce org outside the consent is refused; other sites are fine. A
browser change (click, typing, script, form input) is allowed only when:

- the tool names its tab (a tool without a tab ID, such as a devtools click on the
  selected page, is refused; use a tool that takes a tab ID, or `torque browser -o ORG`);
- that tab was sent to exactly one Salesforce org in this session, that org has a granted
  window, and the tab never showed another org;
- the call itself names no other org (no URL or org argument for a different org); and
- that browser server has had no navigation that did not name its tab.

The gate sees the navigation the session asked for, not what the browser then showed. A
navigation that failed, or a redirect, leaves the tab where the gate cannot see it; that is
the remaining limit, listed below.

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
  key is involved. The gate also requires `approvals/granted/` to be owned by the approver
  and writable only by it, and refuses an `approver_uid` that is the session's own account.
  Tier 2 is not available on Windows (no numeric account IDs); use tier 1 there. Setup:
  create the approver account; set the mode with
  `--verify owner-uid --approver-uid $(id -u APPROVER)`; install Torque and authenticate the
  Salesforce CLI to the client's orgs in the approver account (the grant resolves orgs
  itself); give the approver read access to the workspace; and make each client's
  `approvals/granted/` folder the approver's (`sudo chown APPROVER W/clients/acme/approvals/granted`
  then `sudo chmod 755` on it). On macOS read access can be an inherited ACL, for example
  `chmod -R +a "APPROVER allow list,search,readattr,readextattr,readsecurity,read,file_inherit,directory_inherit" W`;
  on Linux use the equivalent `setfacl` entries. The grant is recorded in the change when the
  approval is used, if the approver cannot write the change record.

## What it stops

With the hook in force, on recognized routes:

- a known org write by Bash, a Torque route or an MCP tool without a granted approval;
- replay of an approval, its use for another command, payload, working folder, org or
  client, its use after its window, after its change record is gone, or after the consent
  stops naming the org ID it was granted for;
- the session granting its own approval through recognized routes (refused by the gate and
  the permission rules; the grant also needs a real terminal outside the session and a typed code);
- a write to an org outside the bound client's consent (whatever route names it), reads of
  another client's folder, and listing every client (`torque client list`);
- a browser change in a tab that is not bound to one org with a granted window (as far as
  the gate has seen that tab), and navigation to a Salesforce org outside the consent;
- reads of record data or debug logs the consent does not cover, in every recognized form;
- org access for a client without active, signed-off consent;
- a production approval of any kind (browser windows included) without an independent
  before-state or a written recovery path;
- edits to the mode, consent and approval files and reads of the approval key, and the
  Salesforce CLI's credential, alias and configuration folders and installation. The consent,
  consent-evidence and approval records of a Torque workspace, and the approval key, are
  guarded from recognized tools in every mode where the hook runs, `full` included;
- an approval used with its files changed, and a use that cannot be recorded;
- changing an alias or the default org with `sf alias set` or `sf config set`;
- org writes, browser changes and programs the gate cannot check while the consultant's
  prompts are skipped (refused in every permission mode except `default`, `acceptEdits` and
  `plan`; doctor checks that bypass mode is disabled). A hook event with no permission mode is
  treated as prompting, so it asks;
- an `sf` read without an explicit org flag (it would read the default org, which no consent
  names);
- an alias remapped to another org between grant and a Torque-route run (wrapper check),
  and a Torque-route run whose connected state cannot be read.

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
- Actions inside a granted browser window (per window, not per click), actions a URL
  triggers during read-only navigation, and a tab that is not where the gate recorded it: a
  navigation the browser did not complete, a server-side redirect, or a link followed inside
  the page to another org. The gate sees the requested navigation, not its result; a
  PostToolUse check of the page's address would be needed to close this, and is not built.
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
