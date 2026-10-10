# Connected mode with per-write approval

Connected mode lets an AI session do real delivery work for one client: read that
client's orgs, run check-only deploys and tests, capture a before-state, and prepare
each change. Recognized org writes wait for the consultant: the session asks for an
approval of one exact command, the consultant reads it and grants it from their own
terminal, and the session may then run that command once. Each step is recorded in the
client's change record for a second reviewer.

**Tier 1 (the default) protects against accidental actions, not code running as the
agent account.** That account can read the signing key and replace local consumption
records, so exact-command approval and single-use checks depend on those files remaining
intact. Tier 2 separates approval ownership but still has local replay and execution
evidence limitations. Stronger enforcement requires signing, consumption state and
execution authorization outside the agent account, with grants bound to an immutable
workspace identity and a verified org identity. This release does not provide that boundary.

It is opt-in. A workspace that is not in connected mode (`"ai_access": "connected"` in
`workspace.json`, written `"connected-guarded"` while [guarded reads](guarded-reads.md) are on)
behaves exactly as before
(`full` by default, or [build-only](ai-access.md)). Like build-only mode, it is a
best-effort gate on recognized tool calls in Claude Code, not an operating-system
sandbox. The limits are listed below.

## What the session can do

| Route | Examples | Decision |
|---|---|---|
| Local work | file tools, `torque change`, `torque approval request/status/list/log` | allowed |
| Reads of the bound client's approved orgs | `sf data query -o acme-prod`, `sf project retrieve start`, Salesforce MCP query, get and describe tools, `sf api request rest` GET | allowed when the consent covers the org and the data class. Schema and metadata reads need `metadata`. Record data (queries, searches, exports, record gets, REST record and query paths, the same reads in legacy `sfdx` and MCP form) needs `records`; Apex logs need `debug_logs`; a REST path Torque cannot place counts as record data |
| Check-only | `sf project deploy validate`, `--dry-run`, `sf apex run test` | allowed with metadata consent and logged in `clients/<slug>/approvals/activity.jsonl` |
| Guarded reads (off by default) | `torque guarded counts`, `fill`, `config`, `record`, `related` | for an org without record consent: allowed when the workspace has guarded reads on and the consent gives that org the lane's class. See [guarded reads](guarded-reads.md) |
| Org writes | any other `sf`/`sfdx` command with an org flag, `sf api request` other than a plain GET, `torque deploy/data/org/recover`, `jsc` write verbs, Salesforce MCP tools that are not clearly reads | allowed once, by consuming a matching approval |
| Torque browser | `torque browser ... --target-org ORG` (and `torque qa` with an org) | needs metadata and record-data consent and a granted browser window for that org; Torque starts an isolated browser and checks its org (see below) |
| External browser and desktop tools | screenshots, page reads, navigation, clicks, typing, scripts and form input through browser MCP, devtools or desktop servers, and Antigravity's own browser tools (`open_browser_url`, `read_browser_page`, `browser_*` and the rest) | refused: tool arguments, URLs and tab IDs do not establish the current context. The tools are recognized by name, the same names build-only mode blocks ([build-only](ai-access.md#what-build-only-blocks)) |
| Credentials and org listings | `sf org display` and `sf org display user` (they print an access token), `sf org open` with `--url-only`, `-r` or `--json` (a login URL), `sf org generate password`, `sf org login ...`, `sf org auth ...`; `sf org list`, `sf org list auth`, `sf alias list`, `sf auth list`, `sf env list` (every org this machine is logged in to, other clients' included); Salesforce MCP tools named for a token, password or credential, or for listing all orgs | refused, with or without an approval, in every permission mode and whether or not a client is bound. No approval can be requested for them. The reason names the query to use instead: `sf data query --target-org ALIAS -q "SELECT Id, Name, IsSandbox FROM Organization"` (a record read, so the consent must cover `records`). See "Credential and org-listing commands" below |
| Programs the gate cannot check | `git`, `hg`, `gh`, linters/formatters, `sf code-analyzer`, `python x.py`, `node`, `bash script.sh`, `npm run`, `pytest`, `curl` to a Salesforce host, an MCP tool under a neutral name whose arguments name a Salesforce host, any program it does not recognize | classified as unverifiable; the gate requests review in prompting modes. Run untrusted project tooling without Salesforce credentials in an isolated account or container; a prompt does not constrain its environment |
| Approval administration | `torque approval grant/deny`, `torque client consent record/sign-off/suspend`, `torque launch`, `torque workspace ai-access` and `guarded-reads`, `torque approval permissions --write`, `torque guarded policy` and `test-records` changes, `sf alias set`, `sf config set`, an `sf` command whose command words the shell builds at run time (`sf org $X`, `sf org d*`), a command that sets `SF_CONTAINER_MODE` or `SFDX_CONTAINER_MODE`, desktop control (computer use) | refused (`torque client consent show`, which only displays the bound client's own record, stays allowed) |
| A command the gate cannot read the way it runs | one of Torque's own commands with a word the shell builds (`torque workspace ${x:-ai-access} full`, an unquoted `$VAR`, a glob, `--summary=$x`), with `--` where the command takes none (`torque -- workspace ai-access full`), or with PowerShell's `--%`, `@args` or a computed `(...)` argument; any Torque command whose client, initiative or org option gets a value the shell fills in (`--client "$NAME"`: the gate would decide by the text, and the command would run for whatever the variable holds); any Torque command with an option's name cut short (`--target other` for `--target-org other`: the parser of a delegated command such as `torque logs` would take it as that option, and the last one given wins; Torque's own commands refuse a shortened name themselves); an `sf` command whose words are only part of a record or log read (`sf query`, `sf log`), which the CLI offers to complete; an `sf` or delegated Torque command with a value attached to a short option or short options written together (`-XDELETE`, `-oALIAS`, `-to ALIAS`) | refused. Quote text (`--summary 'costs $5'`), write the command's words and option names out in full, and write each option and its value as separate words. A double-quoted variable is accepted as an option's value (`--summary "$TEXT"`) in Bash; in a PowerShell call it is not (see "What Windows PowerShell hands a program") |
| A comment on the line | `sf data query ... --json # -o dev`, `sf project deploy start ... # --dry-run` | Bash and PowerShell drop the words after an unquoted `#`; cmd.exe runs them. A `#` in the middle of a word begins no comment; for Bash that includes one right after the `)` of a substitution or an array (`echo $(date)#x ; sf ...` runs `sf`). The gate reads the line both ways and both readings have to pass: the first example has no org, the second is a write. A trailing comment on a command that names its org changes nothing, and a line that is only a comment has no effect. A command that needs no org (`sf --version # check`) is refused with a trailing comment, because with the comment's words it is no longer that command: write the comment on its own line. Inside a comment, as inside the body of a here-document, nothing is quoting and a backslash does not join the next line: the next line is read as the command it is. A here-document's delimiter is read as Bash reads it, whatever word it is (`<<\!`, `<<'E F'`), and with a here-document on the line each line is also read on its own. A backslash before a Windows line ending (CR LF) is read both ways, because Bash on Linux and macOS ends the command there and Git Bash on Windows joins the lines |
| A command substitution | `echo "$(sf data query ... -o dev)"`, backticks, at any depth, also in the body of a here-document | what it runs is read as a command of its own. Between backticks Bash takes one backslash off before `$`, a backtick and a backslash before it reads the text (`\\"` becomes `\"`, a quote that opens nothing), and before a double quote too inside double quotes; the gate reads the text each of those ways. The gate also reads everything after a substitution's start as command text, so a construct it does not know (it does know quotes, `case` patterns, comments and here-documents) cannot hide a command inside one. A `${...}` expansion is read the same way: the word in its braces has quoting of its own, also inside double quotes (`"${x:-"'"}"` is one word). `tests/test_gate_against_bash.py` checks this against real Bash |
| PowerShell's own quoting | a here-string (`@"` ... `"@`), a block comment (`<# ... #>`), a backtick before a quote, a doubled quote, curly quotes and the no-break space, the stop-parsing token (`--%`), `${any name}`; a command that is not the first word of its statement (`$rows = sf data query ... -o dev`, an assignment to any target and a chain of them such as `$a = $b.'c d' = sf ...`, `return sf ...`, `foreach ($r in sf ...)`, `&{sf ...}`); what a hash literal or a script block holds, under any command (`echo @{rows = sf ...}`, `echo @{1=sf ...}`, `1 \| sort { sf ... }`), also behind a string that holds a subexpression (`"$( "..." )"`) | a PowerShell call, and a `powershell -Command '...'` string in a Bash call, is read as written and once more with PowerShell's quoting written the way Bash writes it and without what stands before the command. Each line is also read on its own, so quoting the gate does not know cannot hide the command on a later line; a line of text inside a here-string that reads as an `sf` command is therefore treated as one. Every reading has to pass. A command inside a hash literal or a script block is asked about as it would be on a line of its own (`echo @{1=python x.py}` runs python before `echo` starts), and so is an assignment wherever it stands (`echo @{1=$env:NAME='...'}` sets the variable for the commands after it); beside a write either one is another statement, and the write is refused. A script block handed to a command that does not run it is asked about too. A method call (`$x.Invoke()`, `[scriptblock]::Create(...)`, `$x.$name()`, `$x.ForEach{ ... }`) is asked about wherever it stands, also as an argument of a command (`echo $x.Invoke()`); reading a property (`$x.Name`) is not a call. The characters PowerShell reads as spaces, quotes and dashes (the line and paragraph separators among them) are the tokenizer's own list. A `#`, `<` or `>` in the middle of a word is part of the word among a command's arguments (`sf ... -o dev>x` names the org `dev>x`, `echo a#b` passes `a#b`) and a comment or a redirection in an expression (`$n = 1#b`); the gate reads the line each way and every reading has to pass, and with more than three such places on a line it asks. An escaped dollar (`` "Paid `$5" ``) is plain text. A word that names PowerShell's store of environment variables, functions, aliases or variables as a drive (`cp env:A env:B`, `cp function:prompt function:sf`, `cd alias:`) is asked about as well: a copy or a move there sets a variable or replaces a command. Reading a variable (`$env:PATH`) is not that. `tests/test_gate_against_powershell.py` checks this against real Windows PowerShell |
| What Windows PowerShell hands a program | `torque logs --target-org dev --since 'x" --target-org other'`, `--since "from $DATE"`, `--source-dir 'C:\my app\'`, `--since 'x',--target-org, other`, `--target-org '' other` | Windows PowerShell builds one command line for a program and does not escape what an argument holds. A double quote inside an argument can end it there, so can the value of a variable inside a double-quoted string, a backslash before the closing quote swallows the words after it, an empty argument is not handed on, and one comma with a space beside it makes the whole list separate words. An argument of the first three kinds is read as words the shell adds later (the read is asked about, one of Torque's own commands is refused); a comma list is read as separate words; an org option with an empty value names no org and is refused. A word that begins with a quoted string ends at its closing quote for PowerShell, so `--since "x"--target-org other` is three words, and the gate reads it so. A variable inside a quoted argument (`"$ID"`, `"$1"`) counts as a value that is not on the line. Write values out, without a double quote inside them. `tests/test_gate_against_powershell.py` checks this with a program that records what it receives |
| A `.cmd` launcher (Windows) | `sf apex get log -o dev --log-id %ID%`, `sf ... 'a&b'`, from PowerShell or Git Bash | `sf` on Windows is usually `sf.cmd`, and cmd.exe reads each argument again on its way to the program: it fills in `%NAME%` (with `x -o other` in the variable, the program gets a second org), a double quote inside an argument ends its quoting, and `&`, `\|`, `<`, `>`, `^` in a word without spaces are its own. An `sf` command with one of these is asked about, and refused when prompts are skipped. A `%NAME%` inside a longer quoted argument (`LIKE '%acme%'`) is asked about only when a variable of that name is set. A line that sets one itself is asked about for that: any assignment in a PowerShell line, and in Bash a variable set for the command or exported before it |
| Text that names a command, in PowerShell | `echo "sf data query -q ... -o dev" # note` | a line of plain words and plain quotes is read the same by PowerShell and Bash, and text is text. When the line holds anything else (PowerShell's own quoting, an assignment, a brace, a parenthesis, a variable, a pipe, a comma, a character outside ASCII) and the text names `sf` or `torque`, the gate reads the text from that name; if that gives a route no other reading found, the consultant is asked, and the call is refused when prompts are skipped. A line with a write is not asked about this way: the write needs its approval |
| Words the shell adds later | an `sf` or delegated Torque read with an unquoted `$VAR`, a brace expansion (its words are on the line, so they are also read as the command they make: `{sf,data,query,-o,dev}` is that query, and `-o dev {-o,other}` names two orgs and is refused), a glob that begins a word, or a double-quoted variable standing alone (`sf data query -q ... -o dev $MORE`); in PowerShell also a splatted `@MORE`, `--%` and a computed `(...)` argument; in Bash a string the shell may translate (`$"..."`); a command with a variable set for it (`X=1 sf ...`), after `export`, or after a statement that sets a variable the next commands read without `export` (`PATH=/x:$PATH; sf ...`, `HOME=/x; sf ...`, `read`, `printf -v`, `declare` or a `for` variable of such a name; an ordinary variable such as `x=5` does not count); `hash -p`, and a variable Bash runs commands from (`PS4`, `PROMPT_COMMAND`, `BASH_ENV`). The same for a variable set in passing: `${HOME:=x}`, arithmetic (`$[ HOME = 9 ]`, `${a[HOME=1]}`), `{HOME}>file`. A name counts as written (`tmp=...` is not `TMP`), except `path` and `cdpath`, which zsh ties to PATH and CDPATH | the read still has to pass (consent and data class), and the consultant is asked as well; refused when prompts are skipped. A write keeps needing an approval for its exact text, and a write beside something the gate can only ask about (`$r = sf project deploy start ...`, `env -C DIR sf project deploy start ...`) is refused: run the write on its own. In PowerShell a comma with a space beside it separates words for the program (`-q x, -o, other` reaches `sf` as `-q x -o other`), and the gate reads it that way |
| A value evaluated a second time | `eval`, a string handed to `bash -c` or `Invoke-Expression`, `PS4` with `set -x`, a PowerShell method call; in Bash also a word given to `read`, `printf -v`, `unset`, `test -v` or `[[ ]]` with an array subscript, and a value expanded with `${y@P}`, `${x:y}` or `${!ref}` | asked about where the text is on the line: the gate reads every word once more with its quotes removed, so a command substitution written in pieces (`'a[$'"(sf ...)]"`) is seen with its org and data class. A value that is not on the line (read from a file, taken from the environment, set in an earlier call) is not seen. The gate reads command lines; it is not a sandbox. Give a session logins only for the orgs of its engagement |
| Out of scope | another client's folder or `--client`, `torque client list`, an org not in the consent (whatever the route), an `sf` call without an explicit org | refused |
| Prompts skipped | any org write, browser change or unchecked program while the session's permission mode is not `default`, `acceptEdits` or `plan` (`bypassPermissions`, `auto`, `dontAsk`, or a mode this version does not know) | refused, even with an approval, which stays unused |

A session that was not started with `torque launch` has no client binding: every org,
client and browser route is refused.

## What each data class covers

The consent names data classes for the client (`--data`), and can add classes for one org
(`--org-data ALIAS=class,class`): `records` for a development sandbox that holds no client
records, say, with `metadata` alone for production. The gate uses the client's list plus the
org's own for a call to that org.

- `metadata`: schema, configuration and code. By decision this includes what comes with
  them and cannot be separated without taking the capability away:
  - Apex, Flow and logic test runs and results, deployment validation and deployment errors.
    A test marked `SeeAllData=true`, or an error message, can name a record's value;
  - who created and last changed a component (`sf org list metadata` prints a name and a
    user id for each, and names the signed-in user when the listing is empty), and the
    client's staff where configuration names them: alert recipients, running users,
    approvers, queue and group members. The Salesforce CLI prints the signed-in username in
    the output of many commands;
  - values written into configuration: a list view or report filter, a field default, a
    custom label, a formula literal;
  - how many records each object holds (`sf org list sobject record-counts`, and
    `torque advisory impact` without `--where`, which also counts the child records that
    point at the object), as plain numbers;
  - files Salesforce stores as metadata: documents, static resources, content assets, email
    templates. A retrieve brings back whatever was uploaded there;
  - who holds which permission: `torque advisory` evidence counts the assignments of a
    permission set and can be given one user id.
  So `metadata` is not a promise that no personal data comes back. A client that cannot
  accept this needs a narrower agreement than this class.
- `records`: record data. Queries, searches, exports and record gets in any word order
  (`sf query data`) or colon spelling, `sf org list users`, `sf cmdt generate fromorg`,
  `torque advisory impact` or `receipt` with `--where` (the count of any filter), REST
  record and query paths, a Tooling API row, and a Tooling query that is not a plain schema
  query: one `SELECT` from one schema entity (`ApexClass`, `CustomField`, `Flow`,
  `FieldDefinition`, `ValidationRule`, `Profile`, `PermissionSet` and the like) that names
  no person (`CreatedBy`, `LastModifiedBy`, `Owner`, a user). A REST path with a `..`
  segment is never read as schema. A REST request is a read only as a GET with options the
  gate knows (`--method GET`, `--header`, `--target-org`, `--include`, `--json`,
  `--api-version`, `--stream-to-file`); with a body, a file, another option, or a word the
  shell fills in, it is a write that needs an approval.
- `debug_logs`: Apex logs: `sf apex get log`, `sf apex list log`, an `ApexLog` row, and a query whose
  source is `ApexLog`. A query that merely mentions the word is classed by what it reads.
- `counts`, `config_records`, `test_records`: per org only, for [guarded reads](guarded-reads.md).

## Setting it up

The workspace owner runs every step, in their own terminal (not through the AI session).
Steps marked "present" check that a person is at a real terminal outside the session.

Each "present" step also prints a six-character code the owner types back.

1. Set the mode (present):
   `torque workspace ai-access connected --approval required --path W`
   (tier 2: add `--verify owner-uid --approver-uid UID`, see below; not available on
   Windows). This also copies the
   rule file `production-approval.md` into `W/.claude/rules/`; leaving connected mode
   removes it. Leaving connected mode is a "present" step too: the session cannot switch
   the mode off.
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
   (fail-closed form, `-I`, matcher, timeout, and that its interpreter exists and can run:
   a hook that cannot start fails open), the effective permission rules across the user
   (`$CLAUDE_CONFIG_DIR/settings.json` when that is set, else `~/.claude/settings.json`),
   project and local settings files, the approval tier, the consent, that each approved
   alias still resolves to its recorded org ID, and runs the synthetic calls in
   "Doctor probes" below through the hook. It also shows the permission profile, the named
   delegates, who performed each recorded setup step (and how), and the client's grants by
   approver kind. The hook only decides; nothing it allows is run. It exits 3 when
   anything is not ready.
7. Start the session (present): `torque launch --workspace W --client Acme [-- claude options]`.
   It writes a launch record for the process it becomes and starts `claude` in the
   workspace with `TORQUE_CLIENT` and `TORQUE_LAUNCH` naming that record. The gate binds a
   call to the client only through that record, and only when it belongs to the hook's own
   process or one of its nearest ancestors with the same start time; `TORQUE_CLIENT` set by
   hand binds nothing. Use one session per client. An unattended session starts from a
   delegated approver's launch binding instead ([delegated approver](delegated-approver.md)).
   The launch starts the host the workspace names (`"host"` in `workspace.json`, Claude Code
   when absent), or the one given with `--host claude|antigravity`. The launch record names
   its host and binds only under that host's hook. Everything on this page was proven under
   Claude Code; under Antigravity it is tested offline only ([hosts](hosts.md)).

### Doctor probes

Six probes run unbound, and with `--client` seven more run bound to that client. The
bound ones bind the way a launched session does: doctor writes a `probe` launch record
for its own process, runs the hook with `TORQUE_CLIENT` and `TORQUE_LAUNCH` naming it,
and removes the record afterwards. The gate answer is the same under both permission
profiles; the last two columns show what the host then does under each.

| Probe | Call | Gate answer | Interactive | Under `claude -p` (unattended) |
|---|---|---|---|---|
| `org_write` | `sf project deploy start` to an org, unbound | deny | refused | refused: the hook denies it, no prompt |
| `read_unbound` | `sf data query`, unbound | deny | refused | refused: the hook denies it, no prompt |
| `check_only_unbound` | `sf project deploy validate`, unbound (refused before anything is logged) | deny | refused | refused: the hook denies it, no prompt |
| `unverifiable` | `python3 doctor_probe.py` | ask | the consultant is asked | refused: the ask has no one to answer under claude -p |
| `admin` | `torque approval grant` | deny | refused | refused: the hook denies it, no prompt |
| `browser_write` | a browser click (`mcp__claude-in-chrome__computer`) | deny | refused | refused: the hook denies it, no prompt |
| `bound_read` | `sf sobject list` on an approved org (a metadata read) | allow | runs | runs only if a read allow rule covers it; otherwise refused under claude -p |
| `bound_write_unapproved` | `sf project deploy start` with no approval | deny | refused | refused: the hook denies it, no prompt |
| `bound_org_outside_consent` | an org the consent does not name | deny | refused | refused: the hook denies it, no prompt |
| `bound_default_org` | `sf sobject list` with no `-o` | deny | refused | refused: the hook denies it, no prompt |
| `bound_other_client` | `torque context` for another client | deny | refused | refused: the hook denies it, no prompt |
| `bound_unverifiable` | `python3 doctor_probe.py` | ask | the consultant is asked | refused: the ask has no one to answer under claude -p |
| `bound_skipped_prompts` | a script with prompts skipped (`bypassPermissions`) | deny | refused | refused: the hook denies it, no prompt |

Doctor also runs these checks through the configured hooks:

| Check | Call | Expected |
|---|---|---|
| `sidecar_write` | a `Write` to `.claude/torque-permissions.json` | deny (the session cannot change its own profile) |
| `sidecar_bash_write` | a Bash redirect to the same file | deny |
| `post_hook_mcp` | the `PostToolUse` hook on a browser (MCP) call | runs and exits 0 |
| `post_hook_failure` | the `PostToolUseFailure` hook on a failed Bash call | runs and exits 0 |

The two after-call checks run when those hooks are wired; they record only approved
calls, so the synthetic ones write nothing. Under the unattended profile doctor also
requires a tier 2 workspace whose approver is a named delegate, the gate hook under
`PostToolUse` and `PostToolUseFailure` with the same command and matcher `.*`, no `ask`
rule on a route the gate decides by approval, and a permission sidecar
(`.claude/torque-permissions.json`) whose `settings_sha256` and `hook_python` still match
`.claude/settings.json`. Run doctor as the agent account the session uses, so the probes
prove that account can run the hook.

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
listed as unlinked. It also lists each approved call's execution record (how it ended), a
delegated approver's grants and denials, launches and the recorded setup steps; every row
names the approver's kind (`human` or `ai`) and where it came from.

What an approval binds: the exact command text (`command_sha256`); the org alias and the
org ID resolved at grant, which the client's consent must still record for that alias, with
the consent still active and signed off, when it is used; the client; the change, whose
record must still load when it is used; the working folder; and the files the command
deploys or loads. Those are every file and folder any payload flag names (every value of a
multi-value flag, attached or separate, legacy `sfdx` spellings and comma lists included),
the data files a `sf data import tree` plan names, the manifest, the project files each
named component comes from (a bundle, such as an Aura or Lightning component, a static
resource or an Experience bundle, binds every file in its folder; a component kept in a
shared file, such as a custom label, workflow rule or sharing rule, binds that whole file;
an object's child binds its object folder), the destructive manifests (the components they
delete need no local file), every package folder for a deploy with no selector, and for an MCP call every file
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
window (at most three times, each logged). A recovery approval (`torque recover exec|run
SNAPSHOT`, `jsc revert exec`, in any argument order) is read with the recovery's own
parser and binds that snapshot's folder, every file in it, and the exact recovery operation
it will run, which the grant screen shows. A recovery command the parser cannot read is
refused. At run time the recovery must load the approved folder and derive the same
operation; a changed snapshot, a newer folder with the same snapshot ID, or a different
operation is refused before anything runs. A revert started by `torque recover` names the
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
fields; both files the recovery restores for Apex, its source and its `-meta.xml`; a
Lightning component's JavaScript and `-meta.xml`; an Aura bundle's definition; a static
resource's body and `-meta.xml`), or be declared new at grant
(`--new-component Type:Name`). A write whose changes Torque cannot list (anonymous Apex,
bulk loads, a deploy with no selector) needs a manual recovery path instead. A before-state
captured from another org, captured after the request, or changed since capture blocks the
grant. The snapshot a Torque wrapper takes inside the approved write never counts.

## Browser windows

In connected mode, only Torque's own browser changes anything in an org:
`torque browser ... --target-org ORG` (and `torque qa` with an org). Browser MCP and
devtools servers and desktop tools cannot read, capture, navigate or change pages in
connected mode. An already-open tab may contain another client's data; neither a tab ID
nor a URL supplied in tool arguments verifies its current context. Use Torque's isolated
Playwright session for browser work.

`torque approval request --browser --minutes 20 --purpose "Add Tier to the Case layout" --org acme-sbx ...`
asks for a window of up to 30 minutes for one org. Clicks cannot be listed in advance, so
the window is per org and time, not per action. In a production org the request needs
`--manual-recovery TEXT`.

When Torque's browser starts in a connected workspace, it resolves the org live and
requires consent for both `metadata` and `records`, the ID the consent records, a granted
window for that org, and the org's My Domain address (recorded with the consent). The
session starts through frontdoor on that
My Domain, so the login hosts are never needed.

Once the page loads, the run reads the signed-in user's Username from the page itself (a
same-origin read) and compares it, ignoring case, with the alias's Salesforce CLI
username; usernames are unique across Salesforce, so a match proves both the org and the
user. A mismatch or an unreadable username stops a connected run before the flow starts.
`torque browser ... --json` (which `torque qa` uses) prints an identity report per cell:
the org and how it was verified, the admin before and after, the user after Login As, and
whether the admin was restored. No session URL or session ID reaches the output, the run
folder or the logs. Under a window a delegated approver granted, Torque's browser runs
headless only: the gate and the browser both refuse `--headed`, and a `DEBUG`, `PWDEBUG`
or `DEBUG_FILE` setting that could print the session URL or force a visible browser is
refused before the browser starts.

Every host is enforced below the page: Torque launches the browser with host-resolver
rules that deny every host name (Salesforce or not, including IP literals, `localhost` and
names written with a trailing dot) except the approved org's own exact host names (its My
Domain, Lightning, Setup, site and file hosts, and its Visualforce hosts for its own and its
managed packages' namespaces; no wildcard, so a production org's rules never admit one of
its sandboxes, or the reverse) and one static, read-only content host the Lightning UI
loads (`static.lightning.force.com`). The login hosts (`login.salesforce.com`,
`test.salesforce.com`) do not resolve. The browser runs with no proxy and with service
workers blocked. A request or redirect hop to any other host fails before it is sent,
whatever its method.

On top of that, every request the route handler sees (each navigation, frame and
background call) is checked before it is sent, with the same host list: the approved
org's hosts for any method, the static host for GET, HEAD or OPTIONS only (a POST to it is
refused), and no other host. An allowed request goes on unchanged: the browser sends it
and follows its redirects itself, and Torque never sends a copy of a request. An attached
operator browser (`TORQUE_BROWSER_CDP`) cannot be set up this way and is refused in
connected mode.

The host list has not been checked against a live Lightning page load (Setup and a record
page) in this build; that check is part of the live rehearsal. If Lightning needs another
host, that exact host is added and listed here.

The session lives only as long as its authorization. Every request the handler sees, reads
included, reads the window and the consent again first, with no cache. When the window
ends, or either data category is removed, the consent is suspended or no longer usable,
or the window is withdrawn, Torque refuses the request, closes every page and the browser
context, and the run stops.

## Credential and org-listing commands

Some `sf` commands hand the session a credential. `sf org display` prints the org's access
token (and with `--verbose` its auth URL). `sf org open --url-only` prints a URL that signs in
without a password. `sf org generate password` prints a password. The `sf org login` commands
take a credential, can print tokens with `--json`, and can point an alias at another org.
`sf org list` and `sf alias list` show every org this machine is logged in to, other clients'
orgs included, and `sf org list --json` can also print their access tokens. None of these is
a read, and none is a write an approval can cover. The gate refuses them for the session, and
`torque approval request` refuses to create a request for them. The consultant runs them in
their own terminal.

To check which org an alias points to, the session runs
`sf data query --target-org ALIAS -q "SELECT Id, Name, IsSandbox FROM Organization"`. It is a
record read, so the client's consent must cover `records`.

What is refused:

- `org display` and `org display user`, with any flags.
- `org open` when it prints its URL: with `--url-only`, `-r` (alone or leading a group such as
  `-ro ALIAS`), `--urlonly` or `--json`; with `--flags-dir` (a file there can set either flag);
  with a word the shell could turn into such a flag (a `$` or brace expansion, or a glob that
  can match a file named like an option); and when `SF_CONTAINER_MODE` or
  `SFDX_CONTAINER_MODE` is set in the session's environment, because the CLI then prints the
  URL instead of opening a browser. A command that sets one of those variables is refused
  too. `sf org open` with none of these opens the consultant's browser and stays a write
  that needs an approval.
- `org generate password`, every `org login` variant, and every `org auth` subcommand.
- `org list` (alone or with `auth`), `auth list`, `alias list` and `env list`.
  `org list limits`, `metadata`, `metadata-types`, `users` and `sobject record-counts` read one
  named org and stay reads; without an org flag they are refused like any other read of the
  default org.
- The legacy spellings under `sf` or `sfdx`: `force:org:display`, `force:org:list`,
  `force:org:open`, `force:user:display`, `force:user:password:generate`, `force:auth:...`,
  `auth:...` and `force:alias:list`.
- The words in any order and in the colon spelling, because the CLI accepts both:
  `sf display org`, `sf org:display`. `sf help org display` and `sf which org display` only
  describe a command and pass.
- With a flag before the command words (`sf --json org list`). The CLI wants the command
  first, but the gate does not rely on that: it reads the words wherever they are, and an
  `sf` line that starts with a flag and then names a command is never local work.
- A shortened command. The CLI completes a command on its own when only one command fits
  the words and flags given: `sf display --verbose -o ALIAS` runs `org display`. So a command
  whose words all come from the name of one refused command is refused as that command:
  `sf display`, `sf user`, `sf password`, `sf login`, `sf web`, `sf list`, `sf alias`, and
  `sf org` with no further word. `sf open` is refused when it prints its URL. The reason names
  the full command it could become.
- Each of these as `sf.cmd`, `sf.exe` or `sf.ps1`, by a path, through `npx @salesforce/cli` or
  the CLI's `run.js`, behind `env`, `nohup`, `time`, `xargs`, `bash -c`, `eval`, `$(...)` or
  backticks, and chained or piped with other commands. In a PowerShell tool call they are
  also read in the forms build-only mode reads: the backtick escape, backslash paths,
  `cmd /c`, `Invoke-Expression`, `powershell -Command`, a script block and `-EncodedCommand`.
- An `sf` command whose first three command words hold a `$` or brace expansion or a glob
  (`sf org $X`, `sf org d*`): the shell picks the command after the gate decides, so it is
  refused and no approval covers it.
- A Salesforce MCP tool whose name has `token`, `password`, `credential`, `secret` or
  `frontdoor`, or `display` with `org` or `user` (refused as a credential), or `orgs` with
  `list` or `all` (refused as a listing). This is read from the tool's name only.

The gate reads the command line of a tool call. It does not sit between the Salesforce CLI
and the org, and it is not a sandbox. Known forms that are not refused:

- A command the gate cannot read is asked about, not refused: a script or `python -c` that
  runs `sf org display`, a command piped into a shell (`echo ... | bash`), a program name built
  at run time (`$SF org display`), PowerShell's `Start-Process sf -ArgumentList ...`, and a
  quoted command string given to `cmd /c` in a Bash call. In a prompting mode the consultant
  sees the command and can allow it. In any other mode it is refused.
- A script can read the Salesforce CLI's own files, where the tokens are stored. The gate
  keeps recognized tools away from those folders. It does not inspect scripts.
- Another command's `--json` output may carry auth fields (`sf org create scratch`,
  `sf org resume scratch`). Those are org writes that need an approval, so read the command
  before granting it. This has not been checked against the CLI's output.
- `SF_CONTAINER_MODE` set where the gate cannot see it, such as a shell profile that the
  tool's shell loads, still makes an approved `sf org open` print its URL.
- A Salesforce MCP server under a neutral name. Its tools are not recognized as Salesforce
  tools unless an argument names a Salesforce host, and then the call is only asked about.

## Two approval tiers

- **Tier 1, `hmac` (default): protection against accidental actions.** The consultant and the session share one OS account.
  Approvals are signed with a key at `~/.config/torque/approval.key` (Windows:
  `%APPDATA%\torque\approval.key`), created at the first grant. The gate and the permission
  rules keep recognized tools away from the key, but **a script the session writes and runs
  can read the key and forge an approval**, and on macOS a script can drive a terminal
  (AppleScript, `tmux send-keys`) to answer the grant prompt. Tier 1 records intent and
  catches mistakes; it is not a boundary against a session that writes code to get around it.
  The same account can remove consume markers and replay grants within their validity.
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
  Tier 2 is also what a [delegated approver](delegated-approver.md) needs: an approver
  account (a person or an automated reviewer) that grants and denies without a terminal,
  for non-production orgs, so a session can run unattended.

## What it stops

With the hook in force, on recognized routes:

- a known org write by Bash, a Torque route or an MCP tool without a granted approval;
- reuse of an approval while its local consume marker remains intact, its use for another
  command, payload, working folder, org or client, its use after its window, after its change
  record is gone, or after the consent
  stops naming the org ID it was granted for;
- the session granting its own approval through recognized routes (refused by the gate and
  the permission rules; the grant also needs a real terminal outside the session and a typed code);
- a write to an org outside the bound client's consent (whatever route names it), reads of
  another client's folder, and listing every client (`torque client list`);
- reads and changes through external browser, desktop or devtools tools, a change by Torque's own browser
  without a granted window for its org, any request of Torque's browser to another
  Salesforce org;
- a recognized command that prints or takes a credential (`sf org display`,
  `sf org open --url-only`, `sf org generate password`, `sf org login`) or lists every org on
  the machine (`sf org list`, `sf alias list`), with or without an approval. The forms read
  and the forms that get past are in "Credential and org-listing commands";
- recognized reads of metadata, record data or debug logs the consent does not cover for
  that org, in any word order or spelling the CLI accepts, with or without a variable set
  for the command;
- leaving connected mode from the session: the gate refuses the command in every spelling it
  reads (a word built at run time, `--`, a line continuation, a quoted operator, a
  PowerShell escape), and the command itself needs a person at a real terminal;
- org access for a client without active, signed-off consent;
- a production approval of any kind (browser windows included) without an independent
  before-state or a written recovery path;
- edits to the mode, consent and approval files and reads of the approval key, and the
  Salesforce CLI's credential, alias and configuration folders and installation. The consent,
  consent-evidence and approval records of a Torque workspace, and the approval key, are
  guarded from recognized tools in every mode where the hook runs, `full` included. In
  connected and build-only mode an error inside that check blocks the call; in `full` mode
  it lets the call through ([build-only](ai-access.md));
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

- **Redirect hops.** Playwright does not call the route handler for a redirect hop the
  browser follows, so only the resolver rules apply to a hop. A hop can therefore reach the
  approved org's hosts or the static content host with any method: a 307 or 308 redirect
  can carry a POST body to `static.lightning.force.com`. That host is a read-only content
  service holding no org data, so the POST cannot write to any org. A hop to any other
  host does not resolve.
- **A redirect chain already in flight.** A chain that started before the window ended or
  the consent was suspended completes, within the hosts above. The next request the handler
  sees is refused and the context closes.
- Code executed inside a permitted process (scripts, Git hooks, linter plugins, search
  preprocessors, text-processing programs and runner configuration). The gate now classifies
  extensible tooling as unverifiable and generated permissions include a backstop, but no
  separate prompt is guaranteed when nested code
  runs. Review is not isolation. Run untrusted tooling in a separate account or container
  without Salesforce credentials, auth-directory mounts, tokens or access to credentialed
  processes; merely changing environment variables does not remove file-based credentials.
  Torque does not launch such a sandbox. Regenerate existing connected permission settings
  with `torque approval permissions --workspace W --write` to install the new backstop.
- In tier 1, a script reading the key and forging an approval, or driving a terminal to
  answer the grant.
- In tier 2, a script deleting a consume marker to run the same exact command again within
  its window (bounded by 15 minutes and the exact binding).
- A program the session places ahead of `sf` on `PATH` through a route the gate does not
  see, or a writable Salesforce CLI installation changed by a script; doctor warns about both.
- A Salesforce MCP server registered under a neutral name. A call is classified as Salesforce
  by its server or tool name. Under a neutral name it is local work, with no consent check and
  no approval, unless a string argument names a Salesforce org or login host; then the gate
  asks about it (and refuses it when prompts are skipped). A call that names the org only by
  an alias, a record ID or a SOQL string is not seen. Register Salesforce MCP servers under a
  name that says so, or leave them out of a connected session.
- A credential printed by a command the gate cannot read, and the other forms listed at the
  end of "Credential and org-listing commands".
- What `metadata` covers ("What each data class covers"), and what a write the consultant
  approved prints: anonymous Apex prints whatever its script prints, with a debug log. Read
  the command and the script before approving.
- A double-quoted variable right after an `sf` option that takes no value (`--json "$X"`):
  the gate does not know which of sf's options take a value, so it reads `$X` as one.
- PowerShell beyond what is listed under "Credential and org-listing commands": it is read
  as text. `Start-Process ... -ArgumentList`, a script or `python -c` is asked about, not
  refused; so is a command whose name is in a variable or behind an alias (`& $c ...`,
  `Set-Alias q sf; q ...`). The owner commands still need a person at a real terminal.
- A PowerShell line is also read as Bash reads it, and every reading has to pass. An `sf`
  command written with a backtick (continued over lines, or with an escaped quote inside
  it) is refused or asked about although PowerShell runs it as one command. Write it on
  one line with plain quotes.
- A `%NAME%` that a `.cmd` launcher fills in from a variable the gate cannot see: one set
  only inside a shell that the host keeps between calls. (A variable set on the same line is
  asked about; one set in the environment the gate runs in is seen.)
- The gate cannot tell which PowerShell runs a line. It reads an argument the way Windows
  PowerShell 5.1 hands it to a program, which is the cautious reading; PowerShell 7 escapes
  arguments, and a read that is asked about for this reason would have run as written there.
- Commands built at run time after a program has been permitted, configured subprocesses
  (`tar --to-command`, `rsync -e`, `zip -TT`, GNU `sed`'s `e`), and every route
  [build-only mode](ai-access.md#what-it-cannot-stop) lists as unparsed.
- Actions inside a granted browser window in the approved org (per window, not per click),
  including actions triggered by navigation in Torque's browser.
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
| Unattended-host addendum (rows below) | Checked 2026-09-24 against Claude Code 2.1.282, driving `claude -p` with `CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT` and `CLAUDE_CODE_SSE_PORT` unset (a fresh headless session), from a scratch workspace with its own `.claude/settings.json` | D3, D12, D13, D14, D15 |
| A `PreToolUse` hook `permissionDecision: "allow"` with no matching `ask`/`deny` permission rule | Runs the tool under `claude -p --permission-mode default`. The session ends normally, no hang (`echo probe-allowed` ran, exit 0, about 8 seconds) | D13 (`gate_connected.allow_json`) |
| A matching `ask` permission rule after a `PreToolUse` hook already returned `allow` | The rule still applies: the tool is declined, not run. The session still ends normally in about 9 seconds, no hang; only `PreToolUse` is logged for that call, `PostToolUse` never follows | D3 (`GATED_ASK` design), D13 (unverifiable-command backstop) |
| An unanswerable `ask` under `-p --permission-mode default` (no hook decision, no matching rule, default `--permission-prompts host`) | A read-only-shaped Bash command (`echo`, `ls` inside the working directory) runs without prompting. A write-shaped command (`touch`, a `>` redirect) is refused at once, with Claude Code's own text: "This session is non-interactive, so there was no prompt for you to approve it." Neither shape hangs | D13; sharpens "the hook not running" in "What it cannot stop" with the read/write split |
| An `sf`/`torque` write wrapped in an interpreter or shell form (`python -m torque ...`, `env sf ...`, `exec sf ...`, `bash -c "sf ..."`) under the unattended profile | Matches a backstop `ask` rule (`Bash(python:*)`, `Bash(env *)`, `Bash(exec *)`, `Bash(bash:*)`, one of GATED_ASK's own INTERPRETER/INTERPRETER_FAMILIES siblings that stays in every profile), not the gate's own `sf`/`torque` route rule. That `ask` is unanswerable under `-p`, and a write-shaped command is refused at once (the row above), even on a route the gate itself would have allowed: the wrapping form, not the underlying command, is what the host rule sees first. Unattended sessions must call `sf` and `torque` directly, never wrapped | D3 (`GATED_ASK`/backstop design) |
| The working-directory sandbox (separate from the permission-ask system) | `ls /nonexistent-probe` (outside the session's cwd) is refused immediately, before any permission decision; only `PreToolUse` is logged, no `PostToolUse` or `PostToolUseFailure`, no hang | D13 (a refusal here is not a gate or permission-rule decision) |
| `PostToolUse` input keys for Bash under `-p` | `session_id`, `transcript_path`, `cwd`, `prompt_id`, `permission_mode`, `effort` (`{"level": ...}`), `hook_event_name`, `tool_name`, `tool_input`, `tool_use_id`, `duration_ms`, `tool_response` (`stdout`, `stderr`, `interrupted`, `isImage`, `noOutputExpected`). No exit-code field; `PostToolUse` fires only for exit 0 | D14 (`execution.outcome`) |
| `PostToolUseFailure` input keys, and where a Bash exit code appears | Fires instead of `PostToolUse` for a nonzero exit. Same base keys as `PostToolUse` (`prompt_id` and `effort` included) plus `error` (a string starting `Exit code N` on its first line, then stderr) and `is_interrupt` (boolean); no `tool_response` key at all. Verified with `ls ./nonexistent-file-here`, exit 1 | D14 (`execution.outcome`'s exit-code regex reads `error`) |
| A Bash call that hits its own `timeout` tool-input value | Is not killed: the process moves to a background task instead of failing. The hook event is still `PostToolUse`, not `PostToolUseFailure`; `tool_response.interrupted` stays `false` and there is no `exitCode`/`exit_code` field. `tool_response` instead carries `backgroundTaskId` (string) and `timedOutAfterMs` (number), and the process keeps running past the hook until something stops it. Verified with `perl -e '1 while 1'`, `timeout: 2000` | D14: `execution.outcome()` as drafted falls through to `("succeeded", 0)` for this shape, misrecording a timed-out call as succeeded; needs its own case before D14 ships |
| `BASH_MAX_TIMEOUT_MS` | Setting it to `3000`, either as an ambient env var of the `claude` process or via `.claude/settings.json`'s `env` block, did not clamp an explicit `timeout: 30000` on a Bash call in either case (`sleep 15` ran to completion, `duration_ms` about 15100) | D12/D13 test-harness timing; do not rely on it as a hard ceiling over an explicit larger request |
| `CLAUDE_CONFIG_DIR` when set | Relocates the whole config root, not only `settings.json`: pointing it at a directory with no credentials fails the session before hooks or settings load (`Not logged in · Please run /login`); `claude doctor` under the same var independently reports "Not signed in to claude.ai". Not proven to control settings-rule resolution on its own, since login must succeed first | D3 (a tier-2 separate approver account needs its own credentials under a custom `CLAUDE_CONFIG_DIR`, not only a settings file) |
| `--input-format stream-json` without `--output-format stream-json` | Fails fast, no hang: `Error: --input-format=stream-json requires output-format=stream-json.`, exit 1 | D13 test harness |
| `--input-format`/`--output-format stream-json` without `--verbose` | Fails fast, no hang: `Error: When using --print, --output-format=stream-json requires --verbose`, exit 1 | D13 test harness |
| stream-json `result` message shape | `{"type": "result", "subtype": "success", "is_error": false, "result": "<text>", "session_id": ..., "stop_reason": "end_turn", "num_turns": 1, "duration_ms": ..., "permission_denials": [], "usage": {...}, "modelUsage": {...}, ...}`; `--verbose` adds many more fields (cost, subagent stats, timing) | D13 test harness result parsing |

`sf` commands that only read (from the 2.150.6 summaries) and that connected mode
allows for an approved org: `data query`, `data get record`, `data search`,
`data export tree|bulk|resume`, `data bulk results`, `data resume`,
`sobject describe|list`, `org list limits|metadata|metadata-types|users|sobject record-counts`,
`project retrieve start|preview`, `project deploy report|preview`,
`apex get log|test`, `apex list log`, `flow get test`, `logic get test`,
`package installed list`, `package install report`, `package uninstall report`,
`package version list`, `community list template`, `limits api display`,
`cmdt generate fromorg`. `apex tail log` is not a read:
it turns on debug logging (a trace flag) in the org. `org display` and `org display user`
print an access token, and `org list` with no subcommand (or `auth`) lists every org on the
machine: they are not reads and not writes, they are refused ("Credential and org-listing
commands"). So are `org auth show-access-token`, `show-sfdx-auth-url` and
`show-user-password`. Any other command not on this list counts as a write.
