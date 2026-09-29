# Torque engagements: clients and initiatives for org-wide use (design v5)

Status: converged 2026-09-29 after five rounds of adversarial review by four models (Codex GPT-6 Astra, Gemini 3.1 Pro, Kimi K3, Claude Opus 5.5); rulings in the review record below, 2026-09-29. Base: Torque 2.0.0a17 (main).

## 1. Goal, scope, non-goals

Goal: Torque becomes the standard way a whole consultancy (~20 people) organizes delivery work. Every client and every internal initiative is tracked cleanly and separately so nothing is lost, on one shared Linux VM or on a single laptop, with the same model.

In scope: an engagement model with two kinds (`client`, `initiative`); a status board; a loose-material check with copy-only adoption; an end-of-session reminder; per-engagement agent instructions and a generated CURRENT_WORK.md; shared-host permissions; migration from the v1 workspace.

Unchanged product principles: no default global CLI interception or approval tokens; build-only and opt-in connected modes stay as they are (connected mode is the per-client trust ladder); the user's real Salesforce authorization governs real operations; never silently select another client or org.

Non-goals: a server or database; a web UI; a project-management tool (actions link to the firm's ticketing); hosting Git; Torque granting OS permissions (the host does); process containment of an agent inside one engagement (documented as out of scope, section 4.2); automatic deletion of anything.

## 2. Model and schemas

| Schema | Fields |
|---|---|
| `torque.workspace/2` | UUID, name, profile, shared root, clone root, minimum writer version |
| `binding.json` | admin-owned, trusted by the gate: engagement UUID, kind, repository URLs (zero or more) |
| `torque.engagement/1` (state/engagement.json) | member-editable: title, accountable owner, lifecycle state and history, timestamps |
| `control/` (clients only) | approver-owned: org identities with environment class (production, sandbox, scratch, developer), consent, approval grants, the client's connected-mode setting |
| `requests/`, `claims/` (clients only) | worker-writable: approval requests by the requester; single-use consumption markers claims/<id>.consumed created with O_EXCL, so a replay is refused. On a shared host the approval mode is the owner check on control/ (per-person signing keys cannot be verified across homes) |
| `torque.session/2` | engagement UUID, actor (person ID, UID, host), summary, delivery status, status basis (observed, asserted, unknown, untested), evidence references, saved-through point (time, and per bound clone the commit and dirty-file count) |
| `torque.change/2`, `torque.change-event/2` | as today plus engagement UUID and actor; also carry adoption notes and ignore decisions |
| `torque.action/1` | one file per action: ID, text, owner, due date (UTC), link, state (open, done, cancelled), closed_by, closed_at; updated by atomic replace under the engagement lock; history in the change journal |
| `pending/<person>.json` | active since, updated, saved-through point, and per bound clone: unpushed commit count, dirty file count, observed_at |

Readers ignore unknown fields and reject unknown major versions. All times are UTC ISO 8601.

Lifecycle: `active`; `paused` (reason, review date); `closed` (outcome, handoff, disposition of open actions, after close-out checks for unpushed clones, pending markers and unfiled material); `archived` (ordinary writes refused, hidden by default). Reopening is recorded. IDs and paths never change. Lifecycle never grants Salesforce access.

## 3. Layout

```
<W>/                                /srv/torque/<firm> on the VM; ~/torque/<firm> on a laptop
  workspace.json                    admin-owned on the VM
  firm/                             firm overrides of Torque's generic templates and standards
  .index/<group>.json               admin-maintained list of the engagements each group covers (0640)
  clients/<id>/                     location unchanged from v1; on a shared host the folder name is an opaque ID
  initiatives/<id>/                 same shape; laptops keep readable slugs
    binding.json                    admin-owned: ID, kind, repositories (gate trust anchor)
    state/                          member-replaceable: engagement.json, CURRENT_WORK.md, actions/
    AGENTS.md, CLAUDE.md            generated, self-contained (firm hard rules inlined); CLAUDE.md imports AGENTS.md
    context.md, context/            human-maintained knowledge
    sessions/ changes/              append-only records, published without overwrite
    pending/<person>.json           per-person marker: active since, saved-through, clone status
    artifacts/ config/
    control/                        clients only; approver-owned
    requests/ claims/               clients only; approval requests and single-use claims
    .claude/                        engagement-level settings and, on laptops, hooks
    .torque/lock                    engagement lock, created at provisioning
  .torque/                          workspace lock, staging, maintenance flag
~/work/<firm>/<engagement-id>/<repo>/  each person's clone; the path itself identifies the engagement
~/.config/torque/, ~/.local/state/torque/   personal settings, bindings, runtime state (never shared)
```

Credentials, Claude and Codex transcripts and delegate runtime state stay per person in their 0700 home. Evidence for team handoff is published into the engagement; references are engagement-relative or repository/commit/path.

## 4. Access, concurrency, attribution

### 4.1 Permission matrix (VM)

| Path | Owner:group | Mode | Who writes |
|---|---|---|---|
| `<W>`, `workspace.json`, `firm/`, `.index/` | admin:firm | 0755 / 0644 (`.index/<group>.json` admin:<group> 0640) | admin |
| `clients/`, `initiatives/` | admin:firm | 0711 | admin host script |
| `<kind>/<id>/` (engagement root) | admin:<group> | 0750 (members cannot rename or replace children) | admin |
| `binding.json` | admin:<group> | 0644 | admin only; the gate checks owner and mode |
| `state/` (engagement.json, CURRENT_WORK.md, actions/) | admin:<group> | 2770 (setgid, no sticky), files 0660 | members through Torque, atomic replace under the lock (not tamper-proof; host audit logs are the evidence) |
| `AGENTS.md`, `CLAUDE.md`, `.claude/` | admin:<group> | 0644 / 0755 | admin (regenerated by the host script); each person's permission answers are saved at user scope (~/.claude) |
| `.torque/lock` | admin:<group> | 0660 | members lock it; nobody replaces it |
| `sessions/ changes/ pending/ requests/ claims/ artifacts/ context/ config/` | admin:<group> | 3770 (setgid, sticky), files 0660 | members; sticky bit stops deleting or renaming others' files |
| `control/` | approver:<group> | 2750, files 0640 | approver role only |
| Homes | person:person | 0700 | person |

Torque checks the owner and mode of `control/`, `binding.json`, the lock and the engagement root before trusting them, and refuses to proceed on a mismatch. The approver account is a member of every client engagement group (added by the admin host script) so it can reach `control/` and `requests/` through the 0750 root.

On a laptop everything is the single user's, 0700/0600, as today. An admin host script creates engagements (group, folders, ACLs, lock), then runs `torque engagement add`; members cannot create client engagements.

Publication: one helper with two operations. Create-only (records): stage a temporary file in the destination folder itself, set mode and group, rename without overwrite. Atomic replace (only files in state/ and pending/<me>.json, under the engagement lock): stage, set mode and group, rename over. Both fsync the file and the directory. Umask 007 on shared paths. Command lines carry only opaque engagement IDs on a shared host.

### 4.2 Boundaries

Linux groups enforce which people can reach which engagements. Engagement selection inside a session is cooperative routing: Torque refuses to read or write another engagement's paths (one `foreign_engagement` helper over every registered root, used by workspace, changes and the gate), but an agent run by a person in three client groups can reach all three through the operating system. Process containment is out of scope for 2.0 and documented.

### 4.3 Concurrency and attribution

Records are create-only files and need no lock. The replaceable files take the engagement lock (bounded wait). Every record carries person ID, UID, host and assistant. Group-writable records are not tamper-proof; tamper evidence comes from host audit logs. Supported shared storage: the VM's local filesystem.

## 5. Agent instructions and launch

- The launcher starts every session in the engagement folder (so its AGENTS.md, CLAUDE.md and `.claude/` load) and adds the clone as an extra working directory. It sets CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1 so the repository's CLAUDE.md loads too; for Codex (whose --add-dir only grants write access) the generated AGENTS.md tells the agent to read the repository's AGENTS.md. It keeps `execvp`. The pilot supports Claude Code only; Codex follows once its contract tests pass.
- AGENTS.md is generated from Torque's generic template, `firm/` overrides and engagement settings; it inlines the hard rules (one engagement per session, data boundary, client-only operations) and stays under 32 KiB. No dated or status content: that is state/CURRENT_WORK.md, which AGENTS.md points to.
- CURRENT_WORK.md is entirely generated, carries the generator version and time, and is regenerated (debounced) under the engagement lock after session, action, pending and engagement changes, and on demand.
- Codex `--skip-git-repo-check` is passed only for `codex exec` outside Git.

## 6. CLI

```
torque engagement add|list|show|set-state ...
torque client add|list ...          thin alias over engagement (existing contract kept)
torque initiative add|list ...      thin alias
torque board [--kind] [--state] [--owner] [--json]
torque action add|list|done|cancel ...
torque material scan|ignore|adopt ...
torque session add|list|show|pending ...
torque workspace migrate --to 2 [--dry-run]
torque doctor                        (extended: hooks not installed/off/broken, guard contract, writer versions, disk use, claims matching no request)
```

Scoped commands take exactly one of `--client X`, `--initiative X`, `--engagement kind:slug|UUID`. Existing CLI and JSON contracts are preserved. A capability table beneath the parser keeps org operations, deploy and recovery, consent, approvals, connected mode, delegated Salesforce routes and `change verify-deploy` client-only.

## 7. Nothing lost

1. Board: one row per engagement listed in the indexes of the caller's groups, rechecked for access (unauthorized engagements never appear): kind, lifecycle, owner, next and overdue actions, last session, pending markers (with age; stale or unreadable shows unknown), reported check failures. Reads metadata and cached verification with its age; full evidence verification is explicit. Tolerant readers report per-record errors. Budget: under 2 seconds for 50 engagements of typical size. No org calls. `--json`.
2. Material scan: inspects the caller's registered engagements, personal inbox and clones, and reports that coverage. An admin scan additionally lists every folder under clients/ and initiatives/ to find unregistered ones. Flags: unknown files at the workspace root (admin), unregistered engagement folders (admin), broken registrations, abandoned staging, and engagements with pending markers older than 7 days. Git-untracked does not mean orphaned. No symlink following; never enters other homes or credential paths (denylist: ~/.sf, ~/.sfdx, ~/.claude, ~/.codex, .env*, key and certificate files); bounded traversal with incomplete coverage reported. Ignore decisions carry a reason and the content hash (a changed file re-flags). Never deletes.
3. Adopt: copy-only in 2.0: copy the source to a temporary file in the destination folder, refuse credentials (path denylist plus a content check of that copy), hash that copy, publish without overwrite, record an adoption note with the hash, keep the source.
4. Reminder: the launcher publishes pending/<me>.json ("active since"). Pending work = changes since my last session record for that engagement (my files in the engagement, found from a per-person high-water mark, and commits plus dirty files in my clones). Checked at Stop (throttled to once per 10 minutes, systemMessage only, never blocking) and at SessionStart when hooks are installed (managed hooks on the VM), and always at the next torque command. Saving a session clears my marker only up to its saved-through point, so later work survives; a missing or stale marker shows unknown, never done. The marker also carries my clone status for close-out. Fail-open; a per-person and per-workspace switch turns it off; doctor reports not installed, off and broken separately.

Close-out lists every member's clone status with its age; stale or missing reports need a recorded disposition before the engagement closes.

## 8. Guards (connected mode only)

Guards exist only in opt-in connected mode, as today. The engagement model extends them:
- Connected mode is set per client in control/ (the workspace-wide setting becomes a default only). Initiatives can never enter connected mode.
- Deny, not ask, in every non-prompting permission mode for both agents; Codex always deny (it has no ask).
- On the VM, the firm installs Torque's hooks as managed hooks (Claude managed settings with allowManagedHooksOnly and disableBypassPermissionsMode; Codex managed hooks, which skip per-person trust) and permission rules for connector writes. On laptops, doctor reports untrusted or missing hooks.
- The gate derives the engagement from the clone path convention (~/work/<firm>/<engagement-id>/) checked against the admin-owned binding.json repository list; personal bindings are a cache and never trusted by the gate. Unmatched paths under the clone root fail closed.
- a18 updates the gate's protected-record matchers for binding.json (both kinds) and control/, requests/ and claims/ (clients), covering agent writes, deletion, replacement and operations on the containing directories. On a laptop the agent shares the owner's account, so this matcher (not file ownership) is the protection there; laptop regression tests land before any migration.
- Hook behavior and managed-setting values (for example disableBypassPermissionsMode) are a pinned contract: a supported-version list per CLI, contract tests per release, doctor refuses to certify guards on unsupported versions.
- Pattern matching has known bypass classes; OS controls carry the security weight.

## 9. Migration and compatibility

1. a18 ships readers of v2 and barrier-aware writers (every writer checks the maintenance flag and schema under the workspace lock) before any v2 writer exists.
2. Migration runs in a maintenance window: it refuses unless every writer version observed in records from the last 30 days is a18 or later; set the maintenance flag (a18+ writers refuse writes), require a verified off-VM backup newer than 24 hours, migrate (UUIDs, engagement.json, control/ requests/ claims/ split, per-client connected mode, original bytes preserved, defaults labelled "migration default", legacy absolute evidence paths mapped), validate, publish workspace.json schema 2 last, clear the flag.
3. Checkpoints and restart recovery; rollback restores the pre-migration backup only before any v2 write; no reverse migration.
4. Untouched v1 workspaces keep working. `--client` is not deprecated.

## 10. Implementation

One engagement core with kinds, a capability table and a resolved context (shared root, clone root, personal state). Riskiest code: `workspace.py` (`add_client`, `_client_creation_lock`, `atomic_write_new`, `_session_evidence`, `list_sessions`, `client_output_path`); `cli.py` (`_client_args`, `_context_options`, `_dispatch`, `delegated_context`); `changes.py` (`_directory`, `_workspace_of`, `_capture_file`); `gate.py` (`_workspace_chain`, `_decide_root`, `_protected_record`) and `gate_connected._guarded`; `cli_approval.launch`. Reuse the existing root AGENTS.md/CLAUDE.md generation and `.agents/skills` mirroring.

## 11. Testing

Extend creation and crash, isolation and ACL tests across both kinds and both layouts. Add a mandatory multi-UID Linux job (cannot silently skip): non-member enumeration through names, groups and the index, rename and replace attacks on control/, binding.json and the lock, lifecycle changes, CURRENT_WORK regeneration and closing a colleague's action across two UIDs, request, grant, consume and replay across two UIDs, group publication under a hostile umask (022), concurrent writers, the maintenance barrier against an old writer, crash injection, symlink swaps, corrupt records on the board, scan limits, credential refusal in adopt, reminder save-then-change, initiatives refused on every client-only route, offboarding with unpushed work, mixed reader versions. macOS and Windows CI keep the single-laptop layout.

## 12. Operations (VM)

- One pinned Torque install (/opt/torque) referenced by the managed hooks; `minimum_writer_version` enforced; doctor shows writer versions seen in records.
- Off-VM backup of `<W>` and homes (named operator, daily, RPO 24 h, RTO 1 working day) with a restore drill before client data.
- Offboarding runbook: remove groups, end sessions, revoke Salesforce auth under the person's home, reassign owners and open actions, recover unpushed work.
- Salesforce team semantics: shared org identities and environment class in control/; personal auth aliases per home; concurrent changes to one shared sandbox coordinated through change records.
- Doctor reports disk use and growth.

## 13. Release plan and pilot

1. a18: engagement core, capability table, initiatives, v2 readers, maintenance-flag-aware writers, boundary helper, publish helper (create-only and replace), gate matcher update.
2. a19: board, actions, material scan/ignore/adopt, generated AGENTS.md and CURRENT_WORK.md, launcher in the engagement folder.
3. a20: reminder, managed-hook packaging and guard contract tests (Claude Code), migration tool, VM pilot with synthetic engagements. Codex support follows its own contract tests.

Pilot acceptance: a real handoff between two people, an interrupted save, access revocation, a restore drill, and a non-member enumeration check, all passing before client data.

## 14. Risks

P0: cross-engagement leakage (paths, evidence, board); control records writable by members; migration or adoption loses the only copy; VM loss without off-VM backup. P1: shared records and clones diverge; hook behavior drifts between CLI releases; schema or launcher changes break scripts. P2: reminder fatigue; scope creep toward a PM tool.

## 15. Deferred

Commands-to-skills conversion (after the pilot); move/undo adoption; purge automation; retention policy fields; aliases and members fields; heading lint in AGENTS.md; process containment; network filesystems.
