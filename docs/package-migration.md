# JSC package continuation in Torque

The ten JSC Python package roots are the implementation foundation. This is a continuation of the working consulting environment, with client configuration and persisted state moved out of the public distribution. It is not a rewrite of the daily workflow into an advisory-only engine. Internal `jsc_*` imports and compatibility command names remain to reduce churn. The source JusticeServer repositories were read only during the port.

The user's six months of daily use describes the overall JSC working environment. It does not establish that every package, surface, later addition, or migrated integration has six months of live validation. The package-port checkpoints below are offline. A later bounded Salesforce metadata/data acceptance run is recorded in [validation](validation.md); authenticated browsers and model providers remain untested.

## Provenance and selection

`packages/provenance.json` records every selected source file, source-relative path, original SHA-256, current destination SHA-256, and whether it changed. Source revision: `justiceserver-workspace` commit `017c20fdf3b80ef3fc4b85d621e90aa36c21fc81`. Per-file hashes identify the actual inspected source bytes, including any local differences from that commit. New integration files are identified separately. The advisory catalogue retains its own nested provenance back to Torque `3c40916`.

Selected code and generalized tests were imported. The JusticeServer managed application, client documents, org aliases, domain browser flows and test-user records, deployment histories, log archives, credentials, and global hook installation were excluded. Empty browser configuration and synthetic fixtures are intentional template inputs. Existing Apache attribution is retained in source headers and `packages/licenses/`; the third-party browser page-object manifest records a reference dependency, not bundled `node_modules` or a required runtime download.

| Root / import | Preserved behavior | Continuation changes |
| --- | --- | --- |
| `jsc_common` / `jsc_common` | Shared target classification | Call-time selected-client storage and configuration helpers; no default list of private production aliases |
| `revert` / `jsc_revert` | Metadata pre-snapshots, drift checks, per-org concurrency locks, execution wrappers, post-operation classification and polling, supported revert plans, manual recovery guidance | Selected-client state, current explicit target checked against snapshot org, same-interpreter wrapper execution, local stale-lock audit |
| `qa_orchestrator` / `jsc_qa` | 67-row change taxonomy, surface selection, exact-job/component MetaAPI verification, real dispatch, test-user validation | Generic application rows; no ambient global token required; client-specific adapters loaded explicitly; automatic gate-success claim removed |
| `debug_log_analyzer` / `jsc_loganalyzer` | Debug-log parsing, findings and scoring; static code checks | JSON includes analyzed-log coverage; Side-Eff interprets findings rather than treating analyzer exit zero as healthy runtime |
| `memory` / `jsc_memory` | Lesson capture, scrubbing, review/approval storage, ranking, indexing, spool recovery and concurrency | All state resolves dynamically under the selected client; removed CWD lessons/client-context fallback |
| `browser_tests` / `jsc_browser_tests` | Authentication helpers, Login As, CDP support, Lightning component helpers, matrices, assertions, execution, screenshots/reports, registry-based cleanup, optional vision | Generic smoke flow plus explicitly supplied trusted client flow files; empty domain registry; honest empty/unknown coverage and cleanup outcomes |
| `meeting_processor` / `meeting_processor` | Adaptive video frame extraction, transcript parsing/correlation, output assembly, optional frame descriptions | Explicit output directory or selected-client meeting artifacts |
| `adversarial_probes` / `jsc_probes` | Static Apex analysis and probe synthesis | Generic sample inputs and install-aware dispatch; synthesized probes are not claims of executed tests |
| `ai_prompt_regression` / `jsc_ai_prompt_regression` | Output contracts, fixture loading, model replay and validation | Client-scoped prompt staging and explicitly configured fixtures; provider replay remains optional |
| `salesforce_advisory` / `jsc_advisory` | Nonblocking platform advice, read-only evidence collection, checks and receipts | Optional intended-user assignment filter; observations remain distinct from assertions/exceptions and effective-access proof |

Execution breadth includes metadata deploy and quick/resume/abort, anonymous Apex, record create/update/delete/undelete/upsert, tree import, bulk update/upsert/delete/import, permission-set and permission-set-license assignment, package install/uninstall, capture, polling, preview, and supported reverts. Preserving a wrapper does not imply every operation is automatically reversible. The existing capability model remains authoritative: several operations provide forensic snapshots and manual recovery instructions only. Data restoration limitations such as null and unquotable values remain explicit.

## Runtime and client configuration

Torque sets both `TORQUE_WORKSPACE` and compatibility `JSC_ROOT` to the selected **client directory**, such as `<workspace>/clients/example`, before invoking these packages. Helpers resolve paths at call time; an in-process A → B dispatch cannot retain A's imported defaults. The launcher restores the invoking environment afterward. Direct package users may explicitly select the same private directory.

Default state is under `state/revert`, `state/memory`, `state/qa-tests`, `state/meetings`, `state/vision-staging`, `state/prompt-staging`, and `state/audit-logs`. Polling uses `state/revert/_polling_queue`. In Torque scope, stale `JSC_MEMORY_DIR` and `JSC_REVERT_DIR` are ignored. Legacy direct package callers without `TORQUE_WORKSPACE` may still explicitly select those paths. Default state and configuration destinations reject resolved symlinks that escape the selected client. Explicit external configuration paths are trusted operator input, not automatically discovered documents.

| Client input | Direct package environment / behavior |
| --- | --- |
| `config/qa-router.yaml` | `TORQUE_QA_ROUTER`; otherwise bundled generic matrix |
| `config/test-users.json` | `TORQUE_TEST_USERS`; validated schema, selected target alias, file permissions, no credentials |
| `config/browser-flows/` | `TORQUE_BROWSER_FLOWS`, a path-separator-delimited file/directory list; recursive trusted Python modules exporting `FLOW` with `FlowSpec` |
| `config/object-registry.yaml` | `TORQUE_BROWSER_REGISTRY`; supplies actual test-record carriers and cleanup scope |
| `config/ai-fixtures/` | `TORQUE_AI_FIXTURES`; enables real provider replay for selected client fixtures |
| `config/parity.json` | `{ "script": "parity.py", "baseline_org": "explicit-alias" }`; launcher validates a script beneath client config and maps to `TORQUE_PARITY_SCRIPT` / `TORQUE_BASELINE_ORG` |
| Explicit browser connection | `TORQUE_BROWSER_CDP` or compatibility `JSC_BROWSER_CDP`; supplied CDP endpoint |
| Explicit overlay probe | `TORQUE_TEST_RECORD_OBJECT`, optional `TORQUE_TEST_RECORD_FIELD`; no JusticeServer object inferred |

Ordinary QA and execution do not read global bypass tokens. Compatibility token helpers are retained only behind their explicit commands and store state under `state/legacy-tokens`. Browser test-data writes require an identified sandbox/developer org or an explicit run-level production-write choice. An unknown org is not reclassified as safe by a token. Read-only smoke does not require a mutation token.

Browser flows are executable configuration. Domain implementations, seed users and registry carriers must be supplied by the client configuration; bundled synthetic examples are not silently promoted into client test coverage. A custom mutating flow without a cleanup registry returns incomplete cleanup even if its individual checks pass.

## Evidence behavior corrected during integration

- Empty or all-skipped/all-not-applicable browser cells report `NOT_CHECKED`, score zero and nonzero exit. This is not successful coverage.
- Cleanup query failure reports `UNKNOWN`; absent registry for mutating flows reports `NOT_CHECKED`. Either produces a nonzero cleanup outcome. Successful cleanup verification means no survivors among the configured registry carriers; it is not a claim about all objects in an org.
- Side-Eff requests structured log evidence and validates finding/count consistency and positive analyzed-log coverage. P0/P1 findings produce FAIL even when the analyzer completed successfully. Missing or malformed analysis produces ERROR. Clean available logs do not prove the proposed change executed, or that unlogged effects are absent.
- `advisory evidence --user-id` scopes direct and group assignment queries to a validated Salesforce user ID. Without it, assignment observations explicitly refer to anybody in the org. Neither proves full effective user access. Operator not-applicable declarations do not replace an observed absence or unknown result. Report completeness distinguishes observations from accepted assertions and never means actual user access was technically proven.
- The replay scanner reports matching rule/count with redacted details and does not echo the matched credential. Lesson scrubbing remains best effort, and raw operational state stays private.
- Revert execution validates the snapshot org against the current explicit target before building a command, including when `--force` is requested. A stored alias does not override the selected target. Subprocess Python invocations use the running interpreter; fallback execution does not choose an unrelated PATH `jsc` binary.
- In a [connected](connected-approval.md) workspace (2.0.0a15), the revert wrappers (`wrappers/_common.py`) and revert execution (`revert_executor.py`) run a write only when the gate has just consumed an approval for that exact command, and refuse when the target alias now resolves to another org ID than the approved one. They find connected mode themselves (refusing when the workspace configuration cannot be read), verify the approval again, return it when the org cannot be resolved, and a revert runs only the one wrapper command it names under its approval. Dry runs and workspaces not in connected mode are unchanged.
- In a connected workspace (2.0.0a15), the browser session setup (`jsc_browser_tests/auth.py`) checks the org against the consent and a granted browser window, and installs a request guard on the browser context that refuses requests to another Salesforce org. Outside connected mode it is unchanged.
- In 2.0.0a16, the browser runner (`jsc_browser_tests/runner.py`, `suite.py`, `auth.py`, `cli.py`) compares the signed-in Username read from the page with the alias's username. A guarded (connected) run stops on a mismatch or an unreadable username; an unguarded run records the mismatch and continues. Under a window a [delegated approver](delegated-approver.md) granted, the browser runs headless only and refuses debug settings (`DEBUG`, `PWDEBUG`, `DEBUG_FILE`) before it starts. `--json` prints an identity report per cell, which the qa dispatcher (`jsc_qa/dispatcher.py`) passes on and shows. `diagnostics.py` also redacts Salesforce session tokens by their shape and credential fields in JSON.
- In 2.0.0a18, the browser session setup (`jsc_browser_tests/auth.py`) returns to Lightning after Logout As before checking the original user; the suite (`suite.py`) includes the redacted preflight failure in every unexecuted cell's result; and with `--json` the runner (`cli.py`) prints the run folder on stderr so stdout stays JSON.
- In 2.0.0a19, the revert wrappers (`wrappers/_common.py`) refuse a connected write, running nothing, when the approval lookup fails with a workspace error such as the maintenance flag.

## Recovery and local state safety

Successful synchronous metadata deployments with a complete `--metadata` recovery
scope now retrieve and record their resulting state under the org lease. Recovery
compares the same scope, including companion files, with that captured result.
Missing legacy baselines, failed retrievals, missing checksums and unknown state
refuse recovery even with `--force`. Verified differences require that explicit
override. Snapshots without complete before/after evidence, including deployments
finalized asynchronously without a captured result, require manual review.

Cooperating revert wrappers use `~/.torque/org-locks/<canonical-org-id>.json`.
The validated org ID includes its case checksum so case-insensitive filesystems
keep different orgs separate. Aliases, selected clients and snapshot locations do
not affect this key. `TORQUE_ORG_LOCK_DIR` can select one absolute local namespace
for the account; every cooperating process must use the same setting. Finish old
wrapper processes before upgrading to this namespace. Snapshots remain private
to their selected client. The lease covers verification through recovery-wrapper
completion, with validated parent-to-child ownership handoff. It coordinates one
OS account on one machine, not other accounts, machines, Salesforce users, direct
`sf` commands or independent browser writers. It is not a remote org lock and
cannot prevent edits between the deployment and the immediate capture.

Snapshot atomic writes use exclusive random temporary files, complete writes,
file sync and atomic replacement. POSIX creates files at mode 0600 and uses
directory descriptors with no-follow opens through the parent path. Windows uses
`CREATE_NEW` with a protected owner/System DACL at creation, and pins parent
directory handles without delete sharing until replacement completes. Reparse
points are refused. Windows-specific ACL and directory-swap regressions run on
Windows; POSIX mode assertions do not establish Windows permissions. The
directory-swap threat requires an actor able to modify a relevant parent.

Single-record upsert parses quoted assignments once, rejects conflicting external
IDs, and includes the key in a one-record sObject Collections PATCH sent through
`sf api request rest`. Values retain the CLI's literal backslashes, boolean
conversion and JSON relationship objects. The external ID remains a literal key.
A failed or incomplete lookup stops on every org type.
The installed Salesforce CLI must provide that command; there is no create/update
fallback. The pre-query captures evidence and does not choose the write branch.
The server returns the actual insert/update outcome. This operation continues to
have manual recovery only. [Salesforce sObject Collections upsert contract](https://developer.salesforce.com/docs/platform/api-rest/guide/resources-composite-sobjects-collections-upsert.html).

Record update/upsert exit zero only when the mutation and required captures
succeed. Exit 11 indicates a successful sandbox write with missing before-state;
exit 30 indicates incomplete post-capture or an unknown output outcome. A partial
manifest preserves `mutation_succeeded` and the underlying phase separately.
After a confirmed successful write, repair or inspect capture rather than retrying
the mutation. Revert and browser subprocesses decode UTF-8 explicitly and refuse
undecodable structured output. Available invalid bytes are retained privately in
revert evidence, never interpreted as captured field values. Browser diagnostics
redact credential keys recursively and redact Salesforce auth URLs in text.

Lesson promotions, score changes and archival hold a stable OS file lock for the
whole read/modify/write transaction. Corrupt or unreadable active/review/archive
records raise a visible error and preserve their bytes. Promotion publishes the
active list before removing the pending record; a retry after interrupted removal
does not duplicate the active lesson.

## Packaging contract

The root Torque distribution packages all ten import namespaces. Required Python dependencies discovered in production imports are PyYAML for QA/browser configuration, Pillow for meeting-frame operations, and Playwright for browser operations. Most core/advisory/revert/memory/probe code otherwise uses the standard library. Browser binaries, Salesforce CLI, ffmpeg, and Gemini are external capability-specific dependencies; installing Torque does not authenticate, install global hooks, or invoke providers.

Required installed data:

- `jsc_qa/data/*.yaml`
- `jsc_browser_tests/config/*.yaml`
- `jsc_advisory/data/*.json`
- `jsc_ai_prompt_regression/fixtures/**/*.{json,txt}`
- Attribution files from `packages/licenses/`

Fixtures are local format demonstrations. The browser source self-test reports unavailable when omitted from a wheel; it cannot succeed on an empty installed test directory. Source-tree import fallbacks remain in some inherited helpers for compatibility, while real delegated subprocesses use installed namespace imports and the active interpreter.

## Offline validation and limits

The port's focused validation used a private temporary `JSC_ROOT`, no `TORQUE_WORKSPACE` except isolation-specific tests, bytecode disabled, and PATH sentinels replacing `sf`, `sfdx`, `gemini`, `claude`, and `codex`. Sentinel invocations exercise unavailable-tool/failure paths; no real Salesforce or model command ran. Client-isolation tests explicitly switch `TORQUE_WORKSPACE` A → B, seed stale CWD lessons and legacy variables, and verify no cross-client state is selected. Concurrency tests retain four real independent worker processes with bounded timeouts.

Port checkpoint results:

| Validation | Result |
| --- | --- |
| Focused pytest: common + continuation regressions, browser matrix/scoring/cleanup, all memory tests, revert restore-path seam | 155 passed |
| Generalized QA standalone fixture harness | 96 passed |
| Revert CLI integration standalone harness | 33 passed |
| Revert extended wrappers/polling standalone harness | 124 passed |
| Parent final integrated pytest validation | 329 pytest tests + 76 subtests passed |
| Parent final standalone validation | All 12 standalone suites passed; source-specific managed-class examples remain excluded |
| Parent installed CLI smoke | 49 recipes, nine delegate commands, private initialization and two-client isolation passed |

The parent repository's `scripts/test-offline.py` runs pytest-compatible suites and imperative source harnesses separately. Use the final root test output for release-level counts; the table above records observed checkpoints, not a substitute for the final wheel run. Focused port command was `python3 work/package-validation/run-focused.py` in the task workspace; individual repaired harnesses ran as `python3 packages/<root>/tests/<name>.py` with the same private environment and blocked PATH. Exact commands and outputs are retained in the task work directory.

Not established by these tests: authenticated org changes, deploy/revert against a real sandbox, effective user access, real Login As/CDP identity, client-specific flow coverage, actual cleanup of client records, model-provider execution, video-provider performance, or acceptance by a specific firm. These are runtime/pilot validation tasks, not reasons to remove the preserved implementations. a11y and visual regression remain deferred; TAA/Hostile-QA include manual agent workflows rather than an automatic finished pipeline. The public tool preserves those distinctions.
