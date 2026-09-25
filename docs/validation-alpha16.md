# Validation for alpha 16

Alpha 16 adds the [delegated approver](delegated-approver.md) to connected mode. This file
is started during the build and completed at release.

## Review scope

Offline: the full suite on macOS with Python 3.14, the V1 mutation proof below, the
private name check, and the offline contract from an installed wheel. The CI matrix
(macOS, Linux and Windows on Python 3.10, 3.12 and 3.14), the external spec-conformance
review and the live qualification are recorded below when they run.

## Suite results

`python scripts/test-offline.py -q` on 2026-09-25 (macOS, Python 3.14.7), after the V2
fix round: 3377 passed, 10 skipped, 154 subtests passed (alpha 15's baseline: 2661
passed, 5 skipped). Every package self-test passed. The skips: three browser tests that
need Playwright installed, one Windows-only path test, one Linux-only access list test
(`tests/test_default_acl.py::test_real_default_acl_entry_reaches_a_new_file`), the
private name check (the offline runner does not pass its setting; run with plain pytest
it passes, see "Public hygiene"), and four tests, in two files, that drive the real
agent-session check and skip inside an AI session (see "Tests that need a plain
terminal").

## Public hygiene

`tests/test_public_hygiene.py`, including the private name check against a denylist kept
outside the repository, and `tests/test_docs_delegated.py` pass with plain pytest. No new
or changed documentation contains an em dash.

## Edited a15 tests

Every alpha 15 test passes without edits except one behavior test and the release-record
tests the version bump touches. Reconciled against `git diff --stat dea2041 -- tests/`:
six files that existed at `dea2041` differ. Three of them edit existing tests (the four
tests below). The other three only add new tests and change no existing one:
`tests/test_approval.py` gains
`test_production_upsert_still_refused_outright_not_bypassable_via_new_component` and
`tests/test_before_state.py` gains `test_write_components_upsert_matches_a15_both_spellings`
(both R44 regressions), and `tests/test_permissions.py` gains
`test_v2_2_owner_rules_keep_the_absolute_key_rule`, `test_v2_2_delegated_rules_name_no_home`
and `test_v2_2_drift_accepts_the_home_relative_key_rule_only` (with their helper
`_key_rules`). Every other changed file under `tests/` is new in alpha 16.
`tests/test_docs_delegated.py` checks this count and file list against git.

- `tests/test_gate_connected.py::test_hook_end_to_end`. Its first line set only
  `TORQUE_CLIENT=acme` and expected the hook to bind the session to Acme. Requirement 9
  makes the gate bind a connected session to a client only from its launch record, so
  `TORQUE_CLIENT` set by hand no longer binds (`tests/test_launch_binding_gate.py`
  covers that case). The line is replaced by what `torque launch` does: write a human
  launch record for the test process and set `TORQUE_CLIENT` and `TORQUE_LAUNCH` from it.
  Before:

  ```python
  monkeypatch.setenv("TORQUE_CLIENT", "acme")
  ```

  After:

  ```python
  from torque import launch
  record = launch.write_launch_record(w, "Acme", "human")
  monkeypatch.setenv("TORQUE_CLIENT", "acme")
  monkeypatch.setenv("TORQUE_LAUNCH", record["id"])
  ```

  The rest of the test, and what it asserts, is unchanged.
- Release records, edited the way alpha 15 edited alpha 14's. In
  `tests/test_connected_release.py`, `test_version_is_alpha15` becomes
  `test_version_is_alpha15_or_later` (the version is 2.0.0a15 or later and matches
  `pyproject.toml`), and `test_changelog_readme_and_record_for_alpha15` checks that the
  changelog still has the 2.0.0a15 section and that the README names the current version,
  instead of requiring 2.0.0a15 to be the newest. In `tests/test_public_hygiene.py`, the
  no-em-dash list gains `docs/delegated-approver.md` and `docs/validation-alpha16.md`.
  `tests/test_docs_delegated.py` pins 2.0.0a16.

## Invariant verification

Review gate V1 ran a mutation proof on 5b20226: one source change per mutant, run
against the tests that claim the invariant, then against the whole suite, reverted
before the next. 140 mutants: 121 killed, 5 equivalent (2d, 3d, 7a, 12d, 20g, each
shown to be enforced by another check) and 14 surviving as gaps G1 to G12. The gaps
are closed by the tests in the second table. Line numbers are for 5b20226.

| # | invariant | tests | mutations | result |
|---|---|---|---|---|
| 1 | No presence-free path runs inside an AI session (env markers or a `claude` ancestor) | test_delegated_grant, test_delegated_deny, test_launch_binding, test_delegated_setup, test_permission_profiles, test_idempotent_grant, test_presence (agent-session cases) | 1a to 1e: presence.py:55 and :63, delegation.py:210, launch.py:154, cli_approval.py:199 | Killed, all 5 |
| 2 | Every approval record carries approver, approver_uid, approver_kind, approver_model and delegated; the gate refuses one without them | test_delegated_grant identity and kind tests; test_delegated_deny::test_denial_file_is_bound_and_writes_nothing_else; test_status_wait::test_denied_with_the_reason | 2a to 2g: approval.py:61, :1156, :1576, :1814 | Killed; 2d equivalent (`_identity_problem` rejects a None kind or flag) |
| 3 | Delegated grants and denials refuse in tier 1 and outside connected mode | test_delegated_grant_refused_in_tier1; test_denial_refused_for_an_ai_session_and_outside_tier2; test_approver_role_needs_tier2; G1 test | 3b, 3c: delegation.py:234; 3d: approval.py:1184 | Killed 3c; 3b killed by G1; 3d equivalent (`_delegated_proof` enforces owner-uid) |
| 4 | Delegated grants refuse production and unknown orgs; the gate refuses an AI approval recorded as production | test_delegated_grant production and consent tests, gate production tests; G7 test | 4a to 4e: approval.py:1249, :1252, :1253, :1831 | Killed; 4c killed by G7 |
| 5 | The owner's grant needs presence and the typed code and records kind human; a15 tests unedited except test_hook_end_to_end | test_approval::test_grant_needs_operator_and_code; test_owner_grant_keeps_presence_code_and_kind_human | 5a to 5c: approval.py:1014, :1099, :1109; 5d: AST diff of tests/ against dea2041 | Killed, all; 5d holds |
| 6 | A request, payload file set or payload content changed after review refuses the grant; consumption still checks the payload | test_review_binding; test_approval::test_changed_payload_refused; test_delegated_grant_needs_the_reviewed_hashes | 6a to 6g: approval.py:346, :983, :986, :1198, :1244, :1949 | Killed, all 7 |
| 7 | One approval per idempotency key; a key never serves another request | test_idempotent_grant; G2 and G8 tests | 7a to 7h: approval.py:1209, :1269, :1273, :1298, :1345, :1401, :1413 | Killed; 7c by G2, 7f and 7g by G8; 7a equivalent (a different request has a different SHA) |
| 8 | A launch record binds only when it verifies for this process, start time, client, workspace and approver binding | test_launch_binding_gate; test_launch_binding::test_launch_process_check_needs_the_same_start_time; G9 test | 8a, 8c to 8j: launch.py:109, :112, :462, :463, :474, :506, :532 | Killed; 8h killed by G9 |
| 9 | A denial is never read as granted; with no decision the waiter times out, never reads denied | test_status_wait; G10 test | 9a to 9g: approval.py:79, :2516, :2517, :2522, :2535, :2576 | Killed; 9g killed by G10 |
| 10 | Unattended: approved write allow, unapproved deny, unverifiable ask; interactive output unchanged | test_unattended_gate; test_gate_connected::test_hook_end_to_end | 10a to 10f: gate.py:3045, gate_connected.py:56, :254, :310 | Killed, all 6 |
| 11 | No connected flow deletes, recreates or re-modes a folder | test_folder_preservation; test_permission_profiles; test_delegated_setup | 11a to 11f: permissions.py:408, workspace.py:407, :409, approval.py:357, launch.py:344 | Killed, all 6 |
| 12 | `sf org open` needs an approval in every form | test_org_open | 12a to 12e: connected_routes.py:26, :51, :57, :119, permissions.py:20 | Killed; 12d equivalent (the default rule still classifies MCP open tools as writes) |
| 13 | A delegated grant or denial writes nothing into the change record or activity log | test_delegated_grant_writes_nothing_into_the_change_record; test_denial_file_is_bound_and_writes_nothing_else | 13a to 13d: approval.py:1301, :1578 | Killed, all 4 |
| 14 | The delegated grant reads and writes only DELEGATED_READS and DELEGATED_WRITES | test_delegated_reads::test_the_grant_touches_only_documented_paths; G3 test | 14a to 14f: approval.py:408, :1196, :1282 | Killed; 14e and 14f killed by G3 |
| 15 | (R45) With an AI approver delegate, every human-kind record is refused at grant and by the gate | test_delegated_grant R45 tests | 15a to 15d: approval.py:1024, :1716, :1721, :1823 | Killed, all 4 |
| 16 | (R46) Approver-owned or approver-writable control files and folders refuse the gate, the grant and the denial | test_delegated_grant R46 tests; test_idempotent_grant; test_launch_binding; test_launch_binding_gate; G4 and G11 tests | 16a to 16r: approval.py:1190, :1542, :1671, :1673, :1747, :1762, :1765, :1785, launch.py:180, :207, :495 | Killed; 16m by G4, 16o by G11 |
| 17 | (R48) A launch record whose process started before its binding (minus skew) or after it expired (plus skew) does not bind | test_launch_binding_gate R48 tests; G12 test | 17a to 17h: launch.py:510, :517, :519, :521 | Killed; 17g killed by G12 |
| 18 | (R49) A delegated launch refuses any claude option off the allowlist, including CLAUDE_CODE_SIMPLE | test_launch_binding_gate launch-flag tests | 18a to 18h: launch.py:383 to :405, cli_approval.py:200, :236 | Killed, all 8 |
| 19 | (R51) In connected mode, Write, Edit, MultiEdit and NotebookEdit cannot write inside .claude/; worktree copies stay writable | test_unattended_gate sidecar and worktree tests; G5 test | 19a to 19d: gate.py:2468, :2469 | Killed; 19c killed by G5 |
| 20 | (R54) A guarded browser run stops on a username mismatch or unreadable username; no session URL or sid in any output | packages/browser_tests test_identity_report; test_encoded_diagnostic_secrets; test_release_browser_truth | 20a to 20i: runner.py:269, :311, :331, :335, :337, auth.py:250, diagnostics.py:37, :43, :47 | Killed; 20g equivalent (every output boundary redacts again) |
| 21 | (D9) A request never ends with both a valid grant and a delegated denial; an unreadable denial refuses grants | test_delegated_deny marker and corrupt-denial tests; test_status_wait; G6 test | 21a to 21h: approval.py:1021, :1280, :1375, :1552 | Killed; 21f killed by G6 |

Gap closure. Each test passes on the unmutated code and fails under its mutant; every
mutant was reverted with `git checkout -- <file>` and none was committed.

| gap | test | mutation | result |
|---|---|---|---|
| G1 | test_delegated_deny::test_delegated_grant_and_deny_refuse_outside_connected_mode[full, build-only] | delegation.py:234, `mode != "connected"` clause dropped | Killed: both cases grant and deny instead of refusing |
| G2 | test_idempotent_grant::test_a_key_reserved_for_another_request_is_refused_before_publish | approval.py:1269, reservation conflict changed to `if False:` | Killed: request B is granted under A's key |
| G3 | test_delegated_grant::test_delegated_grant_writes_only_the_documented_write_set | grant rewrites consent.json with the same bytes (14e); appends a newline to it (14f); rewrites workspace.json with the same bytes; writes approvals/consumed/probe.json | Killed, all 4 (content, mtime and inode of read-only files; every touched file in DELEGATED_WRITES) |
| G4 | test_delegated_grant::test_r46_grant_refuses_on_its_own_without_a_denied_folder | approval.py:1192, `_grant_delegated`'s R46 `if controls:` changed to `if False:` | Killed: grant succeeds |
| G5 | test_unattended_gate::test_multiedit_and_notebookedit_into_claude_are_denied_through_decide_connected[4 cases] | gate.py:2468, R51 limited to Write and Edit | Killed: MultiEdit on the sidecar and NotebookEdit on the sidecar and on .claude/x.ipynb are allowed (MultiEdit on settings.json stays denied by the guarded-file rule) |
| G6 | test_delegated_deny::test_deny_refuses_an_authentic_grant_even_without_its_decision_marker | approval.py:1552, `_granted_for` scan off | Killed: denial written |
| G7 | test_delegated_grant::test_delegated_grant_refused_when_the_request_records_a_production_org | approval.py:1253, request `org_kind` clause dropped | Killed: grant succeeds |
| G8 | test_idempotent_grant::test_lookup_needs_the_approval_to_carry_the_same_key; ::test_lookup_ignores_a_reservation_others_can_write | approval.py:1413, record key check dropped (7f); approval.py:1401, marker ownership check dropped (7g) | Killed, both: lookup returns the approval |
| G9 | test_launch_binding_gate::test_a_presence_record_naming_a_binding_is_rechecked | launch.py:474, `or "binding_id" in record` dropped | Killed: the record binds |
| G10 | test_status_wait::test_a_denial_wins_over_a_grant_for_the_same_request | approval.py:2516, an authentic grant for the request suppresses the delegated denial | Killed: reads granted |
| G11 | test_delegated_deny::test_deny_refuses_on_its_own_r46_check | approval.py:1542, deny_delegated's R46 `if controls:` changed to `if False:` | Killed: denial written |
| G12 | test_launch_binding_gate::test_a_process_started_just_inside_the_expiry_skew_binds | launch.py:521, upper bound changed to `begun > expires` | Killed: the record does not bind |

`tests/test_daemon_context.py` (3 tests) drives the real agent-session check in
subprocesses and skips inside an AI session; it needs one run from a plain terminal.

## Tests that need a plain terminal

`tests/test_delegated_reads.py` (the trace of every path the delegated grant touches) and
`tests/test_daemon_context.py` (the delegated verbs with no terminal, GUI, keychain or
`HOME`) drive the real agent-session check in subprocesses and skip inside an AI session.
The trace test was widened in the V2 fix round: it also fails on any access outside the
workspace other than the interpreter's own files and a stat of a folder above the
workspace. They run from a plain terminal or CI:

```sh
python -m pytest tests/test_delegated_reads.py tests/test_daemon_context.py -v
```

Result: recorded here when it runs.

## Offline contract from an installed wheel

The wheel built from this tree (`python -m build --wheel`), installed into a fresh virtual
environment and run from a folder outside the checkout with `HOME` pointed there:

```sh
python -m torque.contracts delegated-org-refusal --json
```

2026-09-25: exit 0, `"supported": true`, `"passed": true`; all three cases
(`live-production`, `live-unknown`, `consent-production`) refused with
`org-production-or-unknown`.

## Spec conformance

Review gate V2, an external review of each requirement and global constraint against the
code and tests at e8e352b, found 16 of 23 requirements conforming and 7 partial (3, 10,
13, 14, 16, 19, 23), no Critical finding, six Important and three Minor. The accepted
items were fixed in one round, each with a test written first and seen failing:

| item | fix | tests |
|---|---|---|
| I1 (req. 10, fail closed) | Only a missing permission sidecar is the interactive profile; one that cannot be read or stat'ed, or is invalid, makes the gate deny every org route before an approval is used | test_unattended_gate::test_a_reset_sidecar_denies_even_an_approved_write_and_consumes_nothing, ::test_an_unstatable_sidecar_reads_as_invalid_not_as_absent, ::test_an_unstatable_sidecar_denies_an_approved_write, ::test_a_missing_sidecar_is_still_interactive |
| I2 (req. 3, 14; G06, G08) | An idempotent retry repeats every fresh-grant check (request re-read and hashed, payload derived, consent, live org class) before returning the earlier grant; the lookup and the publish race validate the whole stored record | test_idempotent_grant::test_retry_after_the_request_file_changed_is_refused, ::test_retry_after_the_payload_changed_is_refused, ::test_retry_after_consent_became_unusable_is_refused, ::test_retry_when_the_org_now_reads_as_production_is_refused, ::test_an_exact_retry_rechecks_the_review_before_returning, ::test_lookup_refuses_an_invalid_stored_approval (7 cases), ::test_retry_never_returns_an_invalid_stored_approval |
| I3 (req. 14, 16) | An unreadable payload file or folder refuses (no placeholder hash, no skipped entry); the delegated view and grant refuse a payload path outside the working folder before reading it; the trace test fails on any access outside the workspace other than the interpreter's own files | test_review_binding::test_an_unreadable_payload_file_refuses_instead_of_hashing_a_constant, ::test_an_unreadable_payload_folder_is_not_skipped, ::test_payload_root_check, ::test_a_delegated_grant_never_reads_a_payload_outside_the_working_folder; test_delegated_reads::test_the_grant_touches_only_documented_paths (plain terminal) |
| I4 (req. 23; G08, G13) | An unreadable denials folder and an invalid denial (field types, id and file name, client, the approver's account and uid, `delegated` true, model matching kind, the request's current hash) are errors, exit 2 | test_status_wait::test_an_unreadable_denials_folder_is_an_error_not_a_timeout, ::test_an_invalid_denial_is_an_error_not_denied (12 cases), ::test_a_valid_denial_still_reads_as_denied |
| I5 (req. 13) | The view names the object and external ID field of a bulk upsert (long, short and legacy spellings) and the object, record or deployed components of an MCP write | test_request_view::test_bulk_upserts_normalize_to_object_and_external_field, ::test_legacy_bulk_upsert_names_object_and_external_field, ::test_mcp_write_views_name_their_object_or_target (4 cases), ::test_an_mcp_write_without_an_object_or_target_has_no_components |
| I6 (limit R58) | On Linux, in a delegated tier 2 workspace, files and folders the approver reads open their access-list mask (0640, 0750) when they carry an extended access list | test_default_acl (6 simulated; 1 real `setfacl` test on Linux only) |
| M1 (req. 19) | Doctor summarizes verified launches by actor and each approval identity with its grant and denial counts | test_doctor_delegated::test_doctor_summarizes_verified_launches_and_approval_identities |
| M2 | The edited-test list above reconciled with the diff against `dea2041` | this record |

Two alpha 16 tests changed with these fixes, because they asserted the old behavior:
`test_unattended_gate::test_a_reset_sidecar_gets_no_explicit_allow_even_for_an_approved_write`
(renamed, now requires a denial) and
`test_request_view::test_payload_digest_and_listing_share_one_read_symlink_and_unreadable_parity`
(an unreadable file now refuses in both readers). Neither existed at `dea2041`.

A second external review (at 7c5d11b) found I1 to I5 partial, I6, M1 and M2 resolved,
and two new defects. Fix round V2-3 closed them, each with a test written first and seen
failing:

| item | fix | tests |
|---|---|---|
| I1 | The sidecar is read with `lstat`: a link that cannot be resolved, or a `.claude` entry that is not a folder, is invalid (deny before any approval is used); only a missing entry is the interactive profile | test_unattended_gate::test_v2_3_a_dangling_sidecar_symlink_is_invalid_not_absent, ::test_v2_3_a_dangling_sidecar_symlink_denies_an_approved_write_and_consumes_nothing, ::test_v2_3_a_sidecar_under_a_non_directory_is_invalid |
| I2 | A stored approval's binding fields are checked by type (payload_argv a list of non-empty strings, an 18-character org ID, sha256 call key, command hash, payload digest and reviewed hash, typed flags); an idempotent retry and the publish race return it only when kind, command, call key, command hash, payload digest, payload_argv, working folder, org alias and org ID match the call derived again, else `idempotency-conflict` | test_idempotent_grant::test_v2_3_lookup_refuses_a_stored_approval_with_a_malformed_field (18 cases), ::test_v2_3_retry_refuses_a_stored_approval_bound_to_a_different_call (7 cases), ::test_v2_3_an_exact_retry_still_returns_the_earlier_grant |
| I3 | The delegated payload-root check tests where `sfdx-project.json` resolves before it is opened; a link to an outside file is refused unread | test_review_binding::test_v2_3_a_project_config_link_to_an_outside_file_is_refused_without_reading_it, ::test_v2_3_a_project_config_link_inside_the_working_folder_is_accepted |
| I4 | `denied/` is read with `lstat`: a regular file, a link or ENOTDIR is `denial-unreadable` (exit 2); only a missing folder means no denials | test_status_wait::test_v2_3_a_denials_entry_that_is_not_a_folder_is_an_error_not_a_timeout (3 cases), ::test_v2_3_a_missing_denials_folder_is_still_no_denials |
| I5 | The view reads a path an MCP deploy names as a manifest (its key says manifest, or the file is package.xml) as a manifest and lists its members; grant-side components are unchanged (R44, R64) | test_request_view::test_v2_3_an_mcp_manifest_deploy_view_names_the_manifest_components (2 cases), ::test_v2_3_an_mcp_manifest_deploy_keeps_its_grant_side_components |
| New: human view | `approval show` confines payloads only when the named approver delegate runs it; a human request with a payload such as `../contacts.csv` shows and grants exactly as in a15, and the delegated view of it refuses | test_request_view::test_v2_3_a_human_request_with_a_payload_above_the_working_folder_shows, ::test_v2_3_a_human_request_shows_in_a_workspace_with_no_delegate, ::test_v2_3_the_delegated_approver_view_of_the_same_request_refuses |
| New: doctor | Approval-log rows with identity values of the wrong type are counted as malformed and reported; doctor completes | test_doctor_delegated::test_v2_3_doctor_reports_malformed_historical_identities_without_aborting |

One alpha 16 test changed in this round: `test_unattended_gate::test_an_unstatable_sidecar_reads_as_invalid_not_as_absent`
now denies `lstat` as well as `stat`, since the sidecar check uses `lstat`. It did not
exist at `dea2041`.

A third external review (at b02d0d6) found I1, I3 to I6, M1 and the doctor defect
resolved, and three items open. Fix round V2-4 closed them, each with a test written
first and seen failing:

| item | fix | tests |
|---|---|---|
| I2 residual | An idempotent retry and the publish race also compare payload_check (a gate approval relabeled `wrapper` would skip the gate's payload recheck) and every other binding field the grant writes from the request, the derived call and the org: change, org kind, namespaces, validated job, before state, manual recovery, new components, recovery snapshot, plan and folder, and single use. A mismatch is `idempotency-conflict` | test_idempotent_grant::test_v2_4_retry_refuses_a_stored_gate_approval_switched_to_wrapper, ::test_v2_4_publish_race_refuses_a_stored_gate_approval_switched_to_wrapper, ::test_v2_4_retry_compares_every_derived_binding (12 cases), ::test_v2_4_exact_csv_retry_still_returns_the_earlier_grant |
| Human view | `approval show` confines payloads only when the running account is the named approver delegate and that delegate's kind is `ai`; a named human approver sees the ordinary a15 view | test_request_view::test_v2_4_a_named_human_approver_at_the_running_uid_sees_the_ordinary_view, ::test_v2_4_a_named_ai_approver_at_the_running_uid_is_confined |
| M2 residual | "Edited a15 tests" names six changed baseline files, not five (`tests/test_permissions.py` adds three tests); the count and file list are now checked against git | test_docs_delegated::test_edited_a15_tests_reconciliation_matches_git |

No existing test changed in this round.

Rulings on the remaining V2 items:

- G01 (starting point): the alpha 15 candidate was merged to main as a commit whose tree
  is identical to the candidate's, so alpha 16, built on the candidate, merges on top of
  main with no rebase.
- G02 (public names): legacy predecessor names in provenance and package-migration
  records are permitted by the maintainer. The private name check covers client names
  and passes.
- G07 (edited alpha 15 tests): the edits listed under "Edited a15 tests" stand.
- G09, G14, G16 (test-first evidence, em dash checks before every commit, commit
  trailers): process evidence, recorded here; no code change.

## Live qualification

The live qualification is the test program's Round 0b (a separate approver account
running as a LaunchDaemon, a delegated setup and lock, and unattended sessions against a
developer org). It is recorded here when it runs, including the two-account checks the
offline suite cannot make: the control files readable by the agent account after the
delegated setup and lock, and the hook run as the agent account.
