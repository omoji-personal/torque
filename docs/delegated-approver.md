# Delegated approver

A delegated approver lets a team run connected work unattended. The AI session asks for
an approval of one exact org write, and an independent approver, running in its own OS
account, reviews the request and grants or denies it with no one at a terminal. The
approver can be a person or an automated reviewer (an AI model). Every grant, denial,
launch and setup step records who decided it and of which kind (`human` or `ai`), so the
reviewer's sample can tell them apart.

It builds on [connected mode](connected-approval.md) and changes nothing there unless a
workspace names delegates. The consultant's own grant keeps its presence check, typed
code and grant screen, and records kind `human`. Default (`full`) and build-only
workspaces are unchanged.

## What a team can do with it

- Run a `claude -p` session for one client with no one watching: it prepares a change,
  requests an approval, waits for the decision with `torque approval status --wait`, and
  runs the write once it is granted.
- Put the approval decision in a separate account whose own policy decides what it
  approves, and read every decision back later with `torque approval log`.
- Provision a workspace without a person at a terminal: a setup delegate switches the
  mode, writes the permission rules and records the client's consent and sign-off.
- Start sessions without a person present: the approver issues a single-use launch
  binding and the session claims it.

Delegated approvals are for sandbox, developer and scratch orgs only. A production or
unknown org always goes to a person (see "What it stops and what it cannot stop").

## The two delegate roles

| Role | What it does | Runs as |
|---|---|---|
| `approver` | Grants and denies requests (`torque approval grant --delegated`, `torque approval deny --delegated`) and issues launch bindings (`torque approval launch-binding`) | The workspace's approver account, whose uid is the workspace's `approver_uid` |
| `setup` | Runs the setup steps without a presence check: `torque workspace ai-access connected ... --delegated`, `torque approval permissions --write ... --delegated`, `torque client consent record ... --delegated`, `torque client consent sign-off ... --delegated` | Its own OS account (it may be the approver account) |

Each delegate is a named OS account, recorded in `workspace.json` with its uid and kind.
A delegated step skips the terminal presence check because the operating system proves
who runs it:

- the caller's uid is the named delegate's uid;
- the caller is a separate account from the one that names it and does not own the
  workspace folder (so the account the AI session runs under can never be its own
  delegate);
- `workspace.json` is a regular file owned by root or the delegate and writable by no
  one else;
- no part of an AI session is involved: `CLAUDECODE` and `CLAUDE_CODE_ENTRYPOINT` are not
  set and no ancestor process is a `claude` process (an ancestry that cannot be read
  refuses);
- an `ai` delegate names its model with `--model-id` (letters, digits and `. _ : / @ + -`);
  a `human` delegate gives none.

The gate keeps denying `torque approval grant`, `torque approval deny`,
`torque approval launch-binding`, `torque workspace delegate` and `torque launch` to the
session, in every profile.

## Naming delegates

The workspace owner at a real terminal (presence check and typed code), or an
administrator running as root, names each delegate:

```sh
torque workspace delegate --path W --role approver --account APPROVER --uid "$(id -u APPROVER)" --kind ai
torque workspace delegate --path W --role setup --account SETUP --uid "$(id -u SETUP)" --kind ai
```

The account must exist with that uid. Outside the administrator path, the delegate must
be a different account from the one naming it. The change is recorded in `workspace.json`
(`delegates`, `delegates_changed_at`, `delegates_changed_by`).

## Tier 2 is required

Delegated steps need POSIX (macOS or Linux) and tier 2 approvals:
`"approval_verify": "owner-uid"`. A delegated grant, denial, launch binding or launch also
needs a connected workspace whose approver delegate is the account named by
`approver_uid`. Tier 1 (`hmac`), a workspace that is not in connected mode, and Windows
refuse with reason class `tier-2-required`. Windows has no numeric account IDs, so it has
no tier 2 and no delegated steps.

## Provisioning order

Run these in order. `W` is the workspace, `AGENT` the account the AI session runs under,
`APPROVER` the approver account, `SETUP` the setup delegate's account, `Acme` a synthetic
client with the sandbox alias `acme-dev`.

1. **Accounts (administrator).** Create `APPROVER` (and `SETUP` if it is separate) with no
   GUI login. Install Torque for each account. Authenticate the Salesforce CLI in the
   approver account to the client's non-production orgs: the grant resolves each org
   itself. Set `SF_USE_GENERIC_UNIX_KEYCHAIN=true` for accounts with no login keychain.
2. **Workspace and client (owner).** `torque workspace init W --name "Firm name"`, then
   `torque client add Acme --workspace W --org acme-dev`.
3. **Delegates (owner at a terminal, or root).** Name the approver and the setup delegate
   as above.
4. **Open the workspace to the setup delegate (administrator).**
   - Make `W/workspace.json` owned by `SETUP` (or root), mode 0644.
   - Let `SETUP` create and replace files in `W`, `W/.claude`, `W/.claude/rules` and
     `W/clients/acme` without owning `W`, for example with an access list entry
     (`chmod +a "SETUP allow add_file,add_subdirectory,delete_child" W ...` on macOS,
     `setfacl -m u:SETUP:rwx W ...` on Linux) or a group only `SETUP` belongs to.
5. **Delegated setup steps (`SETUP`, outside any AI session).**

   ```sh
   torque workspace ai-access connected --approval required --verify owner-uid \
     --approver-uid "$(id -u APPROVER)" --path W --delegated --model-id MODEL
   torque approval permissions --workspace W --write --unattended --with-hooks \
     --hook-python /path/to/python --delegated --model-id MODEL
   torque client consent record --workspace W --client Acme --agreed-on 2026-09-30 \
     --evidence agreement.pdf --data metadata --data records --org acme-dev \
     --suspend-contact "Named person" --delegated --model-id MODEL
   torque client consent sign-off --workspace W --client Acme --reviewer "Reviewer name" \
     --delegated --model-id MODEL
   ```

   `--hook-python` names an interpreter the agent account can run; it is recorded in the
   permission sidecar. A human setup delegate leaves out `--model-id`. Leave out
   `--unattended` for the interactive profile.
6. **Lock the controls (administrator).** Remove the setup access from step 4. Make every
   control file owned by root and mode 0644: `workspace.json`, `.claude/settings.json`,
   `.claude/torque-permissions.json`, `.claude/rules/production-approval.md` and
   `clients/acme/consent.json`. Make the control folders root-owned and not writable by
   the approver: `W`, `W/.claude`, `W/.claude/rules`, `W/clients` and `W/clients/acme`.
   Create the approvals folders now, with the owners and modes in the table below
   (a presence launch creates missing ones as the launching account, which leaves
   `approvals/granted/` wrongly owned). Workspaces made before 2.0.0a16 have 0700 root and
   client folders; open them here. Give the approver its read access (below).
7. **Check (as `AGENT`).** `torque doctor --workspace W --client Acme --live`, run as the
   agent account, so its probes prove that account can read the controls and run the
   hook. It shows the profile, the delegates and who performed each setup step.
8. **Prove the refusal contract.** `python -m torque.contracts delegated-org-refusal --json`
   (see "Offline contract command").

Changing delegates or the mode after the lock goes through root (the administrator path)
or through reopening step 4.

### Folders, owners and modes

| Path | Owner | Mode | Notes |
|---|---|---|---|
| `W`, `W/clients/acme` | root, group of the agent account | 1770 (sticky), or 0755 when the agent creates nothing there | Group or other write needs the sticky bit; never owned by the approver |
| `W/clients` | root, group of the agent account | 0750 or 0755 | Never owned by or writable by the approver |
| `W/.claude`, `W/.claude/rules` | root | 0755 | A setup step relaxes these to 0755 only when it created them; an existing folder keeps its mode |
| `workspace.json`, `.claude/settings.json`, `.claude/torque-permissions.json`, `.claude/rules/production-approval.md`, `clients/acme/consent.json` | root | 0644 | Regular files, not links; never owned by or writable by the approver |
| `clients/acme/approvals/` | root, group of the agent account | 1770 (sticky) | The agent writes `activity.jsonl` here; the sticky bit keeps it from renaming the approver's folders |
| `clients/acme/approvals/granted/` | the approver | 0750, group of the agent account | Grants, launch bindings, decision and idempotency markers; the agent reads through the group |
| `clients/acme/approvals/denied/` | the approver | 0750, group of the agent account | Delegated denials |
| `clients/acme/approvals/requests/`, `approvals/consumed/`, `clients/acme/changes/` | the agent account | 0700 | Requests, claim markers and launch records, change records; the approver reads them through its access list |

The approver needs read access to the workspace. On macOS an inherited access list does
it:

```sh
chmod -R +a "APPROVER allow list,search,readattr,readextattr,readsecurity,read,file_inherit,directory_inherit" W
```

On Linux the equivalent is `setfacl -R -m u:APPROVER:rX W` plus a default entry
(`setfacl -R -d -m u:APPROVER:rX W`). A Linux default entry is masked by the mode a file is
created with, and Torque creates the agent's requests and change records 0600 in 0700
folders, so on Linux the approver cannot read files the agent creates later through a
default entry alone. The layout here targets macOS, where access lists are not masked
by mode bits.

Only read-only access list entries belong on the control folders: Torque checks their mode
bits, not their access lists. The workspace must also sit under folders the approver
cannot write, since Torque does not check the folders above the workspace.

### Paths the approver account needs

The delegated grant, denial, launch binding and idempotency lookup read and write these
paths and nothing else (`approval.DELEGATED_READS` and `approval.DELEGATED_WRITES`,
checked by a trace test). `{client}` is the client's folder name and `{cwd}` the
request's working folder, which is often outside the workspace.

| Pattern | Access | Why |
|---|---|---|
| `workspace.json` | read | The delegate, tier and approver are read once from one descriptor |
| `clients` | read (stat) | The control-folder check: not owned by or writable by the approver |
| `clients/{client}` | read (stat) | The same check on the client folder |
| `clients/{client}/client.json` | read | Loads the client |
| `clients/{client}/consent.json` | read | Consent must be active and signed off, and name the org |
| `clients/{client}/changes` | read (stat) | Path resolution into the change record |
| `clients/{client}/changes/*` | read | The request's change record |
| `clients/{client}/changes/**` | read | The change's events, where an owner denial is recorded |
| `clients/{client}/approvals` | read (stat) | Path resolution |
| `clients/{client}/approvals/requests` | read (stat) | Path resolution; the grant never lists it |
| `clients/{client}/approvals/requests/*.json` | read | The request, opened by its exact name |
| `clients/{client}/approvals/granted` | read | Listed for an existing grant before a denial |
| `clients/{client}/approvals/granted/*` | read, write | Grants (`apr-*.json`), launch bindings (`lnk-*.json`), decision markers (`decision-<request_id>.json`) and idempotency markers (`idem-*.json`), each published 0644 |
| `clients/{client}/approvals/consumed` | read (stat) | Path resolution only; the approver never opens anything inside it |
| `clients/{client}/approvals/denied` | read | Listed for an existing denial before a grant |
| `clients/{client}/approvals/denied/*` | read, write | Delegated denials (`dny-*.json`, 0644) |
| `{cwd}` | read (stat) | The working folder must exist |
| `{cwd}/**` | read | The project files, read to derive the payload digest again |

The bare folders are read because each call checks their owner and mode (the control
check) and resolves paths without following links; the approver needs search access on
each of them, not only on the files inside. `consent-evidence/` is not read. The grant
writes no change-record event and no `activity.jsonl` line: the agent writes those when
it uses the approval. An approver program that finds requests with
`torque approval list --json` also lists `approvals/requests/` and checks
`approvals/consumed/`; the inherited read access above covers both.

### Files each setup step writes

`delegation.SETUP_WRITES`, relative to the workspace (`*` is one client folder or file):

| Step | Files | Mode after a delegated step |
|---|---|---|
| `ai-access` | `workspace.json`, `.claude/rules/production-approval.md` | 0644 each; `.claude/rules` becomes 0755 only when this step created it |
| `permissions` | `.claude/settings.json`, `.claude/torque-permissions.json` | 0644 each; `.claude` becomes 0755 only when this step created it |
| `consent-record` | `clients/*/consent.json`, `clients/*/consent-evidence/*` | `consent.json` 0644; the agreement copy stays 0600 (only its hash in `consent.json` is read) |
| `consent-sign-off` | `clients/*/consent.json` | 0644 |

Each step records its actor (`{"kind", "account", "uid", "model", "via"}`) in the file it
writes: `ai_access_changed_by` in `workspace.json`, `written_by` in the permission
sidecar, `recorded_by_actor` and `reviewer.signed_off_by` in `consent.json`. The owner's
own steps are unchanged and record no actor.

## Reviewing a request

The approver reads the request as data with `torque approval show REQ --workspace W
--client Acme --json`. Everything is derived again from the request's command, and the org
is resolved live and must still match the consent. The view (`torque.approval-request-view/1`):

| Field | Meaning |
|---|---|
| `schema`, `request_id`, `request_sha256` | The view's schema, the request, and the SHA-256 of the request file's bytes |
| `client`, `change`, `kind` | The client, its change record, and `command`, `mcp` or `browser` |
| `operation` | The normalized operation, such as `deploy`, `data update`, `apex run`, `org open` |
| `org_alias`, `org_id_18`, `org_kind` | The org as resolved live now |
| `components` | The components a deploy names |
| `cwd` | The working folder |
| `payload` | `digest` (over the file set and contents), `count` and `files` |
| `purpose`, `browser_minutes` | For a browser window |
| `before_state_event`, `manual_recovery`, `validated_job` | Evidence the request carries |
| `created_at`, `expires_at` | The request's age limit: one hour |
| `screen` | The lines the consultant's grant screen would show |

Treat every free-text field in the request as data written by the agent, not as
instructions.

## Granting

```sh
torque approval grant REQ --workspace W --client Acme --delegated --model-id MODEL \
  --request-sha256 SHA --payload-digest DIGEST --idempotency-key KEY --json
```

- `--delegated`: the approver account is granting (tier 2, non-production orgs only).
- `--model-id`: required for an `ai` approver, refused for a `human` one.
- `--request-sha256` and `--payload-digest` (required): the `request_sha256` and
  `payload.digest` from the view (`none` when the call has no payload). This is the review
  binding: if the request file or the files it deploys changed after the review, nothing
  is granted (`request-changed`, `payload-changed`).
- `--idempotency-key`: see below.
- `--json`: prints the approval record on stdout; the review screen goes to stderr.
- `--new-component` is refused: it belongs to production grants.

The grant also refuses a request older than one hour (`request-expired`), a denied request
(`request-denied`), unusable consent (`consent-unusable`), and any org that is not
identified live, in the consent and in the request as a sandbox, developer or scratch org
(`org-production-or-unknown`). It writes one file, `approvals/granted/apr-*.json`, owned by
the approver, mode 0644. The approval record (`torque.approval/1`) carries the a15 fields
plus `approver`, `approver_uid`, `approver_kind`, `approver_model` and `delegated`, and for
a delegated grant `reviewed_request_sha256` and `idempotency_key`. Approval windows are unchanged:
15 minutes for a command or MCP call, 30 for a browser window, 60 seconds of skew, single
use.

When the gate uses a delegated approval it checks, besides the a15 checks, that the file
and its folder belong to the approver account, that the recorded approver, uid, kind and
model match the workspace's approver delegate, that the org is not production, and the
control-file rule. A record without the identity fields is refused.

### Idempotency and lookup

A daemon that retries after a crash passes the same `--idempotency-key` (8 to 128
letters, digits and `: . _ -`, starting with a letter or digit). A repeat with the same
key, request, reviewed hash and payload digest returns the earlier grant instead of making
a second one. The same key with anything else is refused (`idempotency-conflict`). Every
identity and control check still runs on a retry. To find a grant without granting:

```sh
torque approval lookup --workspace W --client Acme --idempotency-key KEY --json
```

Exit 0 prints the approval; exit 1 prints nothing (no completed grant for that key). The
key is stored hashed, as `approvals/granted/idem-<hash>.json`.

## Denying

```sh
torque approval deny REQ --workspace W --client Acme --delegated --reason-class manifest-deny \
  --reason "The manifest names a component outside the engagement" --model-id MODEL --json
```

A delegated denial writes one file, `approvals/denied/dny-*.json` (`torque.denial/1`: `id`,
`request_id`, `request_sha256`, `client`, `change`, `reason_class`, `reason`, `approver`,
`approver_uid`, `approver_kind`, `approver_model`, `delegated`, `denied_at`), owned by the
approver, mode 0644. It writes no change-record event. The reason class is the approver's
own: 2 to 48 lowercase letters, digits and hyphens. Torque checks only its form, so
`manifest-deny` and `scope-out-of-engagement` are examples, not a fixed list.

A request never ends with both a valid grant and a delegated denial. The grant and the
denial each claim `approvals/granted/decision-<request_id>.json` with an exclusive create
just before they publish; whichever comes first owns the outcome. A denial after a grant is
refused (`already-granted`); a grant after a denial is refused (`request-denied`). The
consultant's grant claims the same marker in a tier 2 workspace whose approver is a
named delegate. The
marker is one small file per request and never expires or needs cleanup.

## Waiting for the decision

The session waits with `torque approval status REQ --workspace W --client Acme --wait
SECONDS [--json]` (0 to 3,600 seconds). It only reads: it polls about once a second and
writes nothing.

| Exit | Result |
|---|---|
| 0 | granted |
| 20 | denied |
| 21 | expired |
| 22 | timeout: wait again, never write |
| 2 | usage or workspace error, including an unreadable denial file or a control-file problem |

A denial always wins over a grant. The consultant's own denial reports reason class
`owner-denied`. A request is expired an hour (plus 60 seconds of skew) after it was made
with no grant, or when its grant's window ended unused. `--json` prints
`{"request_id", "state", "detail"}`.

## Launch bindings and launch records

A connected session is bound to one client only by a launch record, never by
`TORQUE_CLIENT` alone. `torque launch` writes the record for the process it becomes (exec
keeps the pid) in `approvals/consumed/<id>.launch` (`torque.launch/1`: `id`, `kind`,
`via`, `client`, `workspace`, `pid`, `pid_started`, `created_at`, and for a binding
`binding_id`, `nonce`, `binding_created_at`, `binding_expires_at` and the approver
identity), and sets `TORQUE_CLIENT`, `TORQUE_WORKSPACE` and `TORQUE_LAUNCH`.

- **Presence launch** (`via: presence`, kind `human`):
  `torque launch --workspace W --client Acme [-- claude options]` at a real terminal, as
  in 2.0.0a15.
- **Delegated launch** (`via: binding`). The approver account issues a single-use binding
  for 1 to 15 minutes:

  ```sh
  torque approval launch-binding --workspace W --client Acme --model-id MODEL --minutes 10 --json
  ```

  It writes `approvals/granted/lnk-*.json` (`torque.launch-binding/1`: `id`, `workspace`,
  `client`, `nonce`, `created_at`, `expires_at`, `approver`, `approver_uid`,
  `approver_kind`, `approver_model`), mode 0644. The agent account (never the approver),
  outside any AI session, claims it:

  ```sh
  torque launch --workspace W --client Acme --delegated --binding lnk-0123456789ab -- \
    -p --input-format stream-json --output-format stream-json --verbose --permission-mode default
  ```

  Only these `claude` options pass through: `-p`, `--print`, `--include-partial-messages`,
  `--replay-user-messages`, `--verbose`, `--no-session-persistence`, and
  `--input-format`, `--output-format`, `--model`, `--fallback-model`, `--effort`,
  `--append-system-prompt`, `--max-budget-usd`, `--json-schema`, `--session-id` and
  `--name` with a value, and `--permission-mode default`. Anything else, or
  `CLAUDE_CODE_SIMPLE` in the environment, refuses (`launch-flag-refused`).
- **Doctor probes** write `via: probe` records for their own process and remove them.

The gate binds a call only when the record `TORQUE_LAUNCH` names belongs to the hook's
own process or one of its nearest ancestors with the same start time (at most 8 `ps`
calls, inside the gate's 5-second budget), for this workspace and client. A binding record
is also checked against its binding (approver-owned, unchanged, from the current approver
delegate), and the process must have started no earlier than 60 seconds before the binding
was created and no later than 60 seconds after it expired.

## The unattended profile

`torque approval permissions --write --unattended` writes the rules a `claude -p` session
needs. A matching `ask` rule still prompts after the hook allows a call, and a `claude -p`
session has no one to answer it, so the unattended profile drops every ask rule on a route
the gate decides by approval (Salesforce CLI and Torque write routes and browser servers).
The interpreter and runner ask rules stay. The profile is recorded in the sidecar
`.claude/torque-permissions.json` (`torque.permissions/1`: `profile`, `written_at`,
`written_by`, `settings_sha256`, `hook_python`) and is honored only in a tier 2 workspace
whose approver is a named delegate.

What the gate decides under the unattended profile:

- an org write with a granted approval: the gate uses the approval and answers with an
  explicit allow, so the call runs;
- an org write without one: deny, no prompt;
- a program the gate cannot check: ask. Under `claude -p` nobody answers, and Claude Code
  refuses a write-shaped command at once. Call `sf` and `torque` directly: a wrapped form
  (`python -m torque`, `env sf`, `bash -c "sf ..."`) matches an interpreter ask rule first
  and is refused.

The interactive profile is unchanged. Doctor runs these probes through the hook; the gate
answer is the same under both profiles (13 probes, the bound ones only with `--client`):

| Probe | Call | Gate answer | Interactive | Under `claude -p` (unattended) |
|---|---|---|---|---|
| `org_write` | `sf project deploy start`, unbound | deny | refused | refused: the hook denies it, no prompt |
| `read_unbound` | `sf data query`, unbound | deny | refused | refused: the hook denies it, no prompt |
| `check_only_unbound` | `sf project deploy validate`, unbound | deny | refused | refused: the hook denies it, no prompt |
| `unverifiable` | `python3 doctor_probe.py` | ask | the consultant is asked | refused: the ask has no one to answer under claude -p |
| `admin` | `torque approval grant` | deny | refused | refused: the hook denies it, no prompt |
| `browser_write` | a browser click | deny | refused | refused: the hook denies it, no prompt |
| `bound_read` | `sf org display` on an approved org | allow | runs | runs only if a read allow rule covers it; otherwise refused under claude -p |
| `bound_write_unapproved` | `sf project deploy start`, no approval | deny | refused | refused: the hook denies it, no prompt |
| `bound_org_outside_consent` | an org the consent does not name | deny | refused | refused: the hook denies it, no prompt |
| `bound_default_org` | `sf org display` with no `-o` | deny | refused | refused: the hook denies it, no prompt |
| `bound_other_client` | `torque context` for another client | deny | refused | refused: the hook denies it, no prompt |
| `bound_unverifiable` | `python3 doctor_probe.py` | ask | the consultant is asked | refused: the ask has no one to answer under claude -p |
| `bound_skipped_prompts` | a script with prompts skipped | deny | refused | refused: the hook denies it, no prompt |

Under the unattended profile doctor also requires the gate hook under `PostToolUse` and
`PostToolUseFailure`, no `ask` rule on a gated route, and a sidecar that still matches
`.claude/settings.json`. Any later edit to `settings.json`, even a legitimate one, shows as
drift: rewrite the rules with the command doctor prints (it keeps `--unattended`).

Doctor also summarizes the approval log (`approval_history` in `--json`): each verified
launch record's actor (account, uid, kind and model) with a count, the number of launches
that are not verified, and each approval identity (actor, uid, kind, model, delegated or
not) with its grant and denial counts.

## Execution records

With the hook also wired under `PostToolUse` and `PostToolUseFailure`, each call that used
an approval is recorded as an `approval_executed` event in the change record, linked to its
`approval_consume` event by `tool_use_id`: `outcome` (`succeeded`, `failed`, `interrupted`
or `unknown`), `exit_status` (from `Exit code N`; none when not observed), `approval_id`,
`request_id`, `consume_event_id` and `approver_kind`. A Bash call that timed out and moved
to the background records `unknown`, never `succeeded`. The after-call hook never blocks.

`torque approval log --workspace W --client Acme [--since DATE] [--json]` lists change-record
events, delegated grants and denials not yet in a change record, launches and setup steps,
oldest first. Every row carries `approver_kind`, `approver_uid`, `approver_model`,
`delegated`, `reason_class` and its source. A launch row carries `via` and `verified`
(true only for a binding launch whose binding still checks out); agent-written launch
evidence is shown, never presented as verified.

## Offline contract command

From an installed wheel, outside the repository:

```sh
python -m torque.contracts delegated-org-refusal --json
```

Exit 0 with `"passed": true` means a delegated grant was refused, with
`org-production-or-unknown`, for an org resolved live as production, one resolved as
unknown and one the consent records as production, using a synthetic resolver that never contacts Salesforce. The
distribution is one wheel, so no other package is needed. On Windows it reports
`"supported": false`.

## Reason classes

A refusal exits 3. With `--json` it prints `{"refused": true, "reason_class", "message"}`;
otherwise `torque: refused (CLASS): MESSAGE`.

| Class | Raised when | What to do |
|---|---|---|
| `agent-session` | A delegated step, grant, denial, binding or launch runs inside an AI session | Run it from the delegate's own account, outside the session |
| `not-delegated` | No delegate for the role, the wrong account, a delegate that owns the workspace folder or is the naming account, `workspace.json` not owned by root or the delegate or writable by others, a missing or unexpected `--model-id`, a control file or folder owned by or writable by the approver, a missing or wrongly owned `approvals/granted/`, or a launch from the approver account | Fix the layout (see "Folders, owners and modes") or the account |
| `tier-2-required` | Windows, tier 1, not connected, or an approver delegate that is not the `approver_uid` account | Set tier 2 with a named approver delegate |
| `org-production-or-unknown` | The org is production or unknown live, in the consent or in the request | The consultant grants it |
| `request-changed` | The request file differs from `--request-sha256`, the review hashes are missing, or the request or its change cannot be read, is dated in the future or cannot be derived | Review the request again |
| `payload-changed` | The files the call uses differ from `--payload-digest` | Review the request again |
| `request-expired` | The request is older than one hour | The session makes a new request |
| `request-denied` | The request was denied, or a denial claimed the decision first | The session makes a new request |
| `consent-unusable` | The consent is missing, pending, suspended or does not cover the org | Fix the consent |
| `idempotency-conflict` | The key was used for another request or other reviewed content | Use a new key |
| `already-granted` | A denial for a request that already has a grant | Nothing: the grant stands |
| `denial-unreadable` | A file in `approvals/denied/` cannot be read or is malformed | Remove it (see "Runbook"); every grant for the client is refused until then |
| `human-grant-needs-human-approver` | A consultant grant in a workspace whose approver delegate is `ai` (the gate refuses such a record too) | Use a workspace whose approver is a person for production work |
| `launch-flag-refused` | A delegated launch passes a `claude` option off the allowlist, or `CLAUDE_CODE_SIMPLE` is set | Remove it |
| `binding-missing` | No launch binding with that ID for the client | Ask the approver for one |
| `binding-invalid` | The binding is malformed, a link, not approver-owned, for another workspace, client or approver, dated in the future or longer than 15 minutes | Ask the approver for a new one |
| `binding-expired` | The binding's window ended | Ask the approver for a new one |
| `binding-used` | The binding was already claimed | Ask the approver for a new one |

`owner-denied` appears only in `approval status --wait` output, for the consultant's own
denial. A delegated denial carries the approver's own class.

## Running the approver as a LaunchDaemon

On macOS the approver runs as a system LaunchDaemon in its own account, with no GUI session
and no terminal. Give it an explicit environment; do not rely on a login shell:

```xml
<key>UserName</key><string>APPROVER</string>
<key>EnvironmentVariables</key>
<dict>
  <key>HOME</key><string>/Users/APPROVER</string>
  <key>SF_USE_GENERIC_UNIX_KEYCHAIN</key><string>true</string>
  <key>PATH</key><string>/path/to/torque/bin:/usr/bin:/bin</string>
</dict>
```

The delegated verbs need no terminal, GUI or login keychain, read the account name from
the system (not `$USER`), and write nothing under `HOME`; they work even with `HOME` unset.
`SF_USE_GENERIC_UNIX_KEYCHAIN=true` keeps the Salesforce CLI off the login keychain,
which a daemon cannot open. A daemon pointed at a tier 1 workspace is refused with
`tier-2-required` before any key is looked up. On Linux use a systemd service with
`User=` and the same variables.

## Runbook

- **A grant or denial crashed midway.** Retry the same decision: the decision marker lets
  the same side proceed, and an idempotency key returns an earlier grant. A marker whose
  grant or denial was never published blocks the other decision for that request; the
  request then expires, and the session makes a new one.
- **`denial-unreadable`.** One unreadable or malformed file in `approvals/denied/` refuses
  every grant for that client until an administrator or the approver account inspects and
  removes it.
- **Doctor reports drift after an edit to `.claude/settings.json`.** Rewrite the rules with
  the exact command doctor prints.
- **After upgrading from 2.0.0a15.** Rerun `torque approval permissions --write` (with
  `--unattended` and `--with-hooks` as before): the new deny and ask rules put existing
  workspaces in drift. Approvals granted by 2.0.0a15 are refused; grant again.
- **A session no longer binds.** A launch record whose process start time cannot be
  read, or no longer matches, binds nothing (fail closed). Relaunch the session.
- **A launch binding was used by another process.** Ask the approver for a new binding;
  the other process is still gated for every write.

## What it stops and what it cannot stop

It stops, on recognized routes and with the hook in force:

- the AI session approving its own writes: every presence-free path refuses inside an AI
  session, and the gate denies the approval, launch and delegation commands to the session;
- a delegated approval of a production or unknown org, at grant and again at the gate;
- a grant of anything other than what was reviewed (request file and payload), a grant of
  a stale or denied request, and a request ending both granted and denied;
- an approval, binding or setup record without its approver identity and kind, or with an
  identity that does not match the workspace's delegate;
- a session binding itself to a client with `TORQUE_CLIENT`, or with another process's
  launch record or an old one;
- a delegated launch that turns off hooks or permissions through `claude` options;
- the session rewriting its own permission sidecar (file tools and Bash alike);
- a headed browser under a window a delegated approver granted.

What it cannot stop:

- **The approver's own policy.** A delegated approver approves what its policy allows.
  Torque checks identity, org kind and the review binding; the policy is the control.
- **Human launch records rely on process identity.** In tier 2 a presence launch record
  is written by the session's own account in the agent-owned `approvals/consumed/`, and
  presence and probe records are not signed. A forged record naming a real ancestor
  process with its real start time binds; writing one needs a write the gate denies plus a
  nested `claude` process. Presence records have no binding-time lower bound.
- **Binding claims live in the agent's folder.** The single-use marker of a launch binding
  is in `approvals/consumed/`, so deleting it allows another claim within the binding's
  window. The agent account can also claim an unused binding first by starting a fresh
  `claude` process after the binding is issued; the real launch then fails `binding-used`,
  and the other session is still gated for every write.
- **Execution records are agent-written evidence.** The consume marker, `activity.jsonl`
  and the change record are written by the agent account, so direct writes can forge an
  execution record (requiring both the marker and the matching consume event makes it
  harder, not impossible). A non-Bash call records `succeeded` with no exit status. A
  call that used a write approval and a browser window records only the approval's
  execution, and a call under two windows records only the last. A hook stopped by its
  time limit while appending could leave a partial last line in the change record.
- **No live org check at use.** As in 2.0.0a15, the gate does not resolve the org again
  when a command or MCP approval is used. An agent that can rewrite its own Salesforce CLI
  aliases could repoint one after a sandbox grant; keep the agent account's `sf`
  configuration out of its own write reach.
- **A shared approver account.** A grant could be replayed across workspaces that share an
  approver account and a client name. Give each workspace its own approver account.
- **Recovery commands.** A delegated grant of `torque recover` carries no recovery binding,
  so the recovery is refused when used. The consultant grants recovery commands.
- **Production work with an AI approver.** A workspace whose approver delegate is `ai`
  refuses every human-kind approval, so production work needs a workspace whose approver
  is a person.
- **The consultant's own denial after a delegated grant.** The consultant's `approval deny`
  does not claim the decision marker, so it can land after a valid delegated grant;
  `status --wait` then reports denied (fail closed).
- **Folders above the workspace and access lists.** Torque checks the owner and mode bits
  of the workspace, `clients/` and the client folder, not the folders above them and not
  access lists.
- **A change-record read error in a narrow window.** An error reading the change record
  during a delegated grant, after the denial check, can surface as a plain workspace error
  (exit 2) rather than `request-changed`. Either way nothing is granted.
- **Clock edges.** Process start times come from `ps` with one-second precision in local
  time. In a daylight-saving fall-back hour a start time can be read an hour off; the
  launch is then refused, and never bound more than 60 seconds plus one hour outside its
  binding. Log rows written within the same second may sort in either order.
- **Linux read access.** Default access list entries do not reach the agent's new
  requests and change records (see "Folders, owners and modes"); the layout here targets
  macOS.
- **Windows.** There is no tier 2, so there are no delegated steps and no launch bindings.
  The process checks on launch records are skipped: naming any existing presence or probe
  record binds.
- Everything connected mode lists under [what it cannot stop](connected-approval.md#what-it-cannot-stop).

The separate approver account, and the agent account holding only approved clients'
credentials, remain the real boundary.
