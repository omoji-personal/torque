# Validation for alpha 16

Alpha 16 adds the [delegated approver](delegated-approver.md) to connected mode. This file
is started during the build and completed at release.

## Review scope

Offline: the full suite on macOS with Python 3.14, the V1 mutation proof below, the
private name check, and the offline contract from an installed wheel. The CI matrix
(macOS, Linux and Windows on Python 3.10, 3.12 and 3.14), the external spec-conformance
review and the live qualification are recorded below when they run.

## Suite results

`python scripts/test-offline.py -q` on 2026-09-25 (macOS, Python 3.14.7): 3329 passed,
9 skipped, 154 subtests passed (alpha 15's baseline: 2661 passed, 5 skipped). Every
package self-test passed. The skips: three browser tests that need Playwright installed,
one Windows-only path test, the private name check (the offline runner does not pass its
setting; run with plain pytest it passes, see "Public hygiene"), and four tests, in two
files, that drive the real agent-session check and skip inside an AI session (see "Tests
that need a plain terminal").

## Public hygiene

`tests/test_public_hygiene.py`, including the private name check against a denylist kept
outside the repository, and `tests/test_docs_delegated.py` pass with plain pytest. No new
or changed documentation contains an em dash.

## Edited a15 tests

Every alpha 15 test passes without edits except one behavior test and the release-record
tests the version bump touches.

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
They run from a plain terminal or CI:

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

Recorded at review gate V2 (an external review of each requirement against the code and
tests).

## Live qualification

The live qualification is the test program's Round 0b (a separate approver account
running as a LaunchDaemon, a delegated setup and lock, and unattended sessions against a
developer org). It is recorded here when it runs, including the two-account checks the
offline suite cannot make: the control files readable by the agent account after the
delegated setup and lock, and the hook run as the agent account.
