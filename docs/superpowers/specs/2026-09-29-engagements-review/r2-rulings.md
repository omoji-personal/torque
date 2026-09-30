# Round 2 rulings (design v2 -> v3)

Scores r2: Codex 74 / 0.85 DISSENT; Gemini 80 / 0.85 CONVERGE; Kimi 82 / 0.80 CONVERGE; Claude 72 / 0.78 DISSENT.

| # | Finding (who) | Ruling | v3 change |
|---|---|---|---|
| 1 | Group-writable engagement root lets members rename control/, lock, instruction files (all four) | ACCEPT | Engagement root 0750 admin:<group>; data subfolders 3770 (setgid + sticky: members cannot delete or rename others' files); Torque verifies owner and mode of control/ and lock before use |
| 2 | Approvals need worker-writable request and consumption state (Codex P1) | ACCEPT | control/ (approver-owned): consent, grants, org identities, connected-mode setting. requests/ and claims/ (3770): requests by the requester; single-use claims created exclusively (O_EXCL), replay refused. Multi-UID request, grant, consume, replay test |
| 3 | Gate matcher only recognizes consent one level below clients/<slug> (Claude P0) | ACCEPT | a18 updates gate matchers (_protected_record, _holds_records) for the new paths and both kinds, with regression tests, before any migration |
| 4 | Connected mode is workspace-wide in code (Claude) | ACCEPT | Mode stored per client in control/; workspace-wide ai_access becomes a default only |
| 5 | Board and scan have no permitted source of names; 0711 plus stat does not stop guessing; /etc/group is readable (Codex, Kimi, Claude, Gemini) | ACCEPT | Admin-maintained per-group index (<W>/.index/<group>.json, admin-owned, 0640, group-readable); board reads the indexes of the caller's groups and rechecks access. On a shared host, folder and group names are opaque IDs (titles live inside engagement.json); laptops keep slugs. Exhaustive unregistered-folder scan is an admin operation; ordinary users get coverage of their registered engagements, reported as such |
| 6 | Universal no-overwrite contradicts mutable files (Codex P1) | ACCEPT | Publish helper has create-only and atomic-replace; replace only for engagement.json, action files, CURRENT_WORK.md, pending/<me>.json, under the engagement lock, preserving mode and group, directory fsync |
| 7 | Umask 027 removes group write (Claude) | ACCEPT | Umask 007 on shared paths; /proc mounted hidepid=2 on the VM |
| 8 | Pending markers miss crashed sessions; overlapping sessions (Codex P1) | ACCEPT | Launch publishes "active since T"; save clears it only up to its saved-through point; a missing or stale marker shows unknown, never done |
| 9 | Close-out cannot see colleagues' clones (Codex P1) | ACCEPT | pending/<me>.json also carries per-clone status (unpushed commit count, dirty file count, observed_at); close-out lists stale or missing reports and records their disposition |
| 10 | Reminder depends on hooks (Kimi P1) | ACCEPT | Stop and SessionStart delivery needs installed hooks (managed on the VM); without them the reminder runs at the next torque command only; doctor reports not installed, off, broken |
| 11 | Clone bindings in ~/.config are agent-writable (Gemini P1) | ACCEPT | Gate derives the engagement from the clone path convention ~/work/<firm>/<engagement-id>/<repo> and the admin-owned engagement.json repository list; bindings are a convenience cache, never trusted for the gate |
| 12 | Old self-installed writers during migration (Kimi P1) vs barrier is overkill (Gemini) | PARTIAL | Keep the maintenance flag honored by a18+ writers; migration refuses unless all writer versions observed in the last 30 days are a18 or later; no other barrier machinery |
| 13 | Action event appends conflict with no-overwrite (Kimi P2) vs simplify actions (Gemini) | ACCEPT simplification | One file per action, updated by atomic replace under the engagement lock (state, closed_by, closed_at); history is in the change journal |
| 14 | Bound reminder scans; regeneration triggers (Kimi P2) | ACCEPT | Per-person high-water mark; CURRENT_WORK.md regenerated (debounced) on session, action, pending and engagement changes, not on every change event |
| 15 | Hash the staged snapshot (Kimi P2) | ACCEPT | Adopt hashes the staged copy |
| 16 | --add-dir does not load hooks, settings or the repo's CLAUDE.md (Claude P1) | ACCEPT | Engagement folder has its own .claude/ (laptop hooks and settings); launcher sets CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1; generated AGENTS.md tells Codex to read the repository's AGENTS.md; Codex --add-dir only grants write access |
| 17 | disableBypassPermissionsMode value differs between doc pages (Claude) | ACCEPT | Value fixed by the pinned-version contract test |
| 18 | Stage agent qualification (Codex B) | ACCEPT | Pilot with Claude Code only (pinned); Codex support after its contract tests pass |
| 19 | Drop 120-line limit (Codex B) | ACCEPT | Keep the 32 KiB limit only |
| 20 | Dirty-file digests (Kimi B) | ACCEPT | Commit plus dirty count |
| 21 | Status basis down to two values (Kimi B) | REJECT | observed/asserted/unknown/untested is an existing Torque principle |
| 22 | Replace the connected-mode gate with OS containment (Gemini B) | REJECT for 2.0 | Connected mode exists today; containment stays in Deferred |
