# Changelog

## 2.0.0a10 - unpublished de-identified mode, Windows and demo breadth update, 2026-09-23

- Every forwarded command (data, deploy, revert, QA, logs, probes, advisory, and
  the rest) now identifies itself as `torque` in its usage and help text and in
  its own descriptions, instead of the tool name it continues from.
- Add a de-identified mode: `torque workspace ai-access build-only` plus a
  Claude Code PreToolUse hook (`python -m torque.gate`) that blocks Salesforce
  org access, client context and workspace-mode tampering while the mode is on.
  The gate resolves paths, unwraps shell wrappers and quoting, also covers
  Torque's own legacy scripts (`jsc`, `jsc-qa` and the rest), `python -m` forms
  and MCP tools whose names indicate Salesforce access, and fails closed on
  evaluation errors and unreadable configuration; it is a best-effort assistant
  guard, not a sandbox. See [de-identified mode](docs/ai-access.md).
- Ship four synthetic offline demo scenarios with matching workflow recipes:
  alert triage, gift and payments, Outbound Funds grants, and requirements to
  build. Each scenario is clearly marked synthetic and ships under the demo
  client's `examples/`.
- Add Windows to the CI matrix alongside macOS and Linux, and add a Windows
  section to the [installation guide](docs/installation.md).
- Fix Windows-only defects the matrix found: `HOME` substitution and backslash
  handling in the gate, CRLF line endings corrupting materialized workflow
  files, a stale-lock race during concurrent single-use consume, non-atomic
  rename behavior, and a memory-package privacy filter that corrupted
  generated lesson ids before they reached disk.
- The distribution and smoke checks compare the catalogue's legacy mappings to
  the 42 retained command names in `docs/workflow-continuity.md`; the four new
  recipes carry a null `source_command`, as the catalogue contract requires.
- `torque recover --help` shows the public `recover <show|preview|run|discard>`
  grammar instead of the underlying `revert ... exec` names.
- Fix a provenance-check encoding gap by making every read/write in the
  release scripts explicit UTF-8.
- Rename the optional delivery-focus workspace profile to `solution-lead` and
  remove firm-specific prose from tracked source; add a hygiene test that
  fails if any tracked text file names a firm again.

1048 offline tests pass (154 subtests).

## 2.0.0a9 — unpublished continuity and verification update, 2026-09-22

- Recheck session evidence during resumption and handoff. Missing, changed or
  unavailable files stay visible while journal history and reported status remain
  intact; large artifacts are hashed in bounded chunks.
- Diagnose malformed session references and metadata observations with the affected
  record instead of crashing during handoff. Check captured byte counts as well
  as hashes; unavailable captures leave the rest of the handoff usable.
- Inspect all selected-client sessions and changes in `doctor`, and identify the
  running installation. Use clearer consulting-workspace and unrun-check wording.
- Keep private workspace/demo creation outside Torque source even when the CLI is
  installed elsewhere, including extracted source distributions.
- Pin offline tests and their child processes to the current checkout. Pytest
  options with operands no longer silently omit standalone fixture suites;
  `--pytest-only` explicitly selects a smaller development run.
- Update packaged session workflows and verify source and clean installed behavior
  separately. See [current validation](docs/validation.md) for scope and limits.

## 2.0.0a8 — unpublished receipt correctness update, 2026-09-08

- Fetch deployment receipts from the explicitly selected org through Salesforce
  CLI's REST transport, preventing cached deployment targets from misattributing
  another org's result.
- Accept equivalent, checksum-validated 15/18-character deployment IDs while
  preserving the raw values; reject malformed IDs and different jobs.
- Distinguish successful receipt retrieval from a failed deployment outcome, and
  exclude HTTP headers, cookies and unrelated transport output from evidence.
- Complete the prepared live Apex qualification and exact fixture cleanup; add
  14 regression tests and retain current host-access and browser coverage limits
  in [alpha 8 validation](docs/validation-alpha8.md).

## 2.0.0a7 — unpublished practical testing update, 2026-09-07

- Publish complete new clients atomically so interrupted creation can be retried;
  preserve existing clients and concurrent creators' work.
- Include bounded firm and selected-client working notes in client handoffs, with
  accurate Unicode character limits and existing journal/evidence sections.
- Read customized workflow text from the explicitly selected private workspace,
  falling back to the packaged recipe when a local file is absent.
- Generate honest Apex null/empty test drafts with typed overload arguments,
  declaration-scoped methods, explicit unresolved assertions and visible unsupported
  cases. Preserve edited outputs; honor source API versions and shorten long names.
- Report generated drafts as unexecuted QA work, including missing or truncated
  output, instead of declaring passing tests. Remove invented bulk/FLS scaffolds.
- Extend installed workflow checks to verify scoped notes and actual customized
  recipe lookup. See current validation for tested runtimes and live-test limits.

## 2.0.0a6 — unpublished evidence consistency update, 2026-09-07

- Reject a successful deployment summary when supplied component/test details
  report failures or inconsistent completion counts. Preserve the exact job ID
  and partial evidence; summary-only responses remain supported.
- Add current provider data-handling guidance, explicit local/cloud data paths
  and an optional client adoption worksheet. No provider settings are changed
  or new runtime approval gates introduced.
- Incorporate scoped Flow cleanup and string-ID query lessons from the rigorous
  live test cycle. See validation for tested versions and remaining UI coverage.

## 2.0.0a5 — unpublished live-test repairs, 2026-09-07

- Resolve org identity from matching Salesforce Organization data; retain the
  actual IsSandbox value and edition separately from nonproduction behavior.
- Restore only metadata covered by the original selectors, with verified source
  paths, file hashes and complete companion files. Preserve unrelated parent and
  sibling metadata; unverifiable legacy compound captures need manual review.
- Interpret current Bulk API CLI results, retain job identities from failed
  submissions, and distinguish partial application from success in execution and
  polling. Match input CSV line endings without rewriting user data.
- Preserve timeout diagnostics, capture exact-job readback evidence privately,
  and refuse to finalize polling without its evidence bundle.
- Verify the current browser identity before logout, retaining the original
  failed switch; redact encoded Salesforce Setup confirmation tokens.

Bounded live acceptance, fixture cleanup and installed-artifact validation are
recorded in [alpha 5 validation](docs/validation-alpha5.md). Four non-admin browser
cases remain blocked; no publication or universal coverage is implied.

## 2.0.0a4 — unpublished workflow update, 2026-09-07

- Incorporated research into eight operating recipes and the shared tools rule:
  use current official Salesforce guidance and the client's existing delivery,
  test and migration stack; retain native evidence in Torque's engagement records.
- QA now starts with business criteria and intended actors; migration guidance
  distinguishes simulation from target-write acceptance; updates carry packaged
  improvements into private workspaces while preserving local edits.
- Added a concise research-adoption map and an optional tooling-context example.
  This is guided tool composition, not a claim of tested native integrations.
- No new runtime features, dependencies, mandatory records or approval steps.

## 2.0.0a3 — unpublished development build, 2026-09-07

- Added an offline synthetic consulting demo with prepared source, real local
  observations, three unrun acceptance criteria and a native handoff.
- Added optional engagement changes connecting business outcomes, criteria,
  decisions, captured evidence, exact metadata observations and next actions.
- Added client listing, structured CLI output, capability-specific local doctor
  checks and direct deploy/data/org/recover command routes.
- Added workspace workflow upgrades that preserve local edits, track packaged
  baselines, handle interrupted updates and serialize cooperating updates.
- Corrected browser identity, incomplete coverage, cleanup, credential diagnostics
  and artifact-path handling. Named-user behavior requires live qualification.
- Corrected QA incomplete-result exit behavior and browser evidence discovery.
- Corrected bulk-delete field capture and operation lease handling. Bulk recovery
  remains manual; observed scope and limitations are retained in captures.
- Expanded public documentation, product comparison, acceptance protocol and CI.

See [validation](docs/validation.md) for the tested artifact and exact limits. An unpublished local
build and configured CI are not a public release or a passed remote CI run.

## 2.0.0a2 — local continuation baseline, 2026-09-07

Bounded live metadata/data/recovery acceptance in a selected disposable Developer
Edition org; improved create diagnostics, changed-field restoration and permission
assignment result handling. Eight exact metadata jobs succeeded and test artifacts
were removed from active use. This did not establish non-admin browser or Flow UAT.

## 2.0.0a1 — local continuation baseline, 2026-09-07

Continued the reusable JusticeserverClaude framework in Torque, preserving 42
original conversational mappings, adding private employer/client workspaces,
49 recipes, portable review skills, selected generic runtime packages, a session
journal and handoffs. Retired the previous Torque enforcement runtime from the
default product; retained a private checkpoint for historical recovery.
