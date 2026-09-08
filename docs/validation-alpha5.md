# Alpha 5 validation — 2026-09-07

This unpublished candidate was tested on macOS, Python 3.14.3, Salesforce CLI
2.150.6 and one explicitly designated disposable Developer Edition org. A lower
reasoning effort executor ran bounded phases. A separate AI agent audited the
plan, fixture, execution scripts and repairs; the primary agent judged complete
job reports, captures and independently queried state. Failed attempts remain in
the private evidence record. This is bounded acceptance, not a comparative claim
or a production certification.

## Artifact and offline results

- 720 pytest tests and 76 subtests passed. All 12 inherited standalone suites
  produced measured completion. Five optional source-mirror checks within the
  QA standalone suite remain skips; these are not live coverage.
- The offline runner intercepted 17 Salesforce failure-path requests with its
  unavailable-tool stub. No live provider call was used for that suite.
- Provenance verifies 178 inherited files while retaining their original source
  hashes. All 49 recipe adapters, 42 original conversational mappings and 64
  bundled conversation resources remain consistent.
- Wheel and source-archive checks passed: 205 wheel entries and 496 source-archive
  entries. All 194 packaged source files, including 122 Python modules, match the
  reviewed source. Wheel RECORD digests and archive exclusions were independently
  checked; Twine accepted both artifacts.
- A regular alpha 5 installation passed dependency and CLI checks outside the
  checkout. A second fresh environment containing only Torque and its declared
  PyYAML dependency also passed. The installed checks verify help/import paths for nine delegates and six public
  routes. All 49 recipe texts were discoverable. Executed local workflows covered
  the no-org demo, two clients/four changes, failure retention through new processes,
  and customized workspace upgrades.
- Configured macOS/Linux CI and other Python versions have not run remotely.

The tested wheel SHA-256 is
`52feb8dba51bfbf75d84cef89cab423fc3559ec349dd82cb47d1f0af3bf80932`.
The tested source archive SHA-256 is
`decdd666fc56121f162a03c9524c1fe20793b0f8a5fbabe67abb48db93021833`.

## Live observations

Initial deployment, business cases, scoped data recovery, exact-job checks and
evidence continuity ran on alpha 4; repaired metadata recovery, bulk operations
and cleanup use an independently verified alpha 5 wheel. They are not counted
as if every phase ran on both versions. The synthetic Flow fixture was unchanged
between those phases.

| Area | Independently observed result |
|---|---|
| Target and installation | Exact org identity and actual edition established; private client scope and installed artifact verified. |
| Invalid and valid validation | Invalid check-only deployment failed for its intended reason without creating components. Valid check-only deployment succeeded with the exact expected components and left the synthetic objects absent, with checkOnly=true verified. |
| Real metadata deployment | A separate actual deployment succeeded; all 16 fixture components and the active Flow version were checked. |
| Business behavior | Normal creates no child; first entry into Urgent and creation already Urgent each produce exactly one child with correct key, status and owner. Repeated edits and sequential leave/re-entry preserve the same child. |
| Transaction failures | Invalid restricted picklist leaves stored values unchanged. A deliberate child validation failure rolls back its parent update and creates no child; correcting only the fault marker permits the expected outcome. |
| Permission configuration | Profile and every assigned permission set were queried. The synthetic user retained its Minimum Access profile and profile-owned grant, with only the intended fixture permission-set assignment. Queried administrative permissions were false; Contact Preference was read-only. This is not browser FLS execution. |
| Data recovery | Restore the changed Note while preserving a separately changed Name and the sentinel. |
| Metadata recovery | Restore only the selected Note field. The exact recovery job contains that field alone; later sibling-field and parent-object labels remain intact. |
| Bulk import/delete | The original CRLF file imports 201 rows. Deletion captures every exact ID across the 200-record boundary and all 14 described fields. Twelve independently queried before-state fields, including every custom field, match by type and value. LastViewedDate and LastReferencedDate have inventory coverage only. Exact job counts and a fresh queryAll reconcile all 201 deleted IDs. |
| Incomplete bulk capture | An existing sentinel plus an already deleted owned ID causes capture failure before an underlying job is submitted. The sentinel remains unchanged. |
| Partial bulk application | Three updates yield two exact successful IDs and one restricted-picklist rejection. Stored values agree with both result files. Torque retains the exact job ID, returns exit 21 and records applied_partial. |
| Evidence and continuity | Correct exact-job evidence accepted; a nonexistent expected component rejected. Fresh processes retain criteria and failed checks; captured evidence survives source changes, tampering is detected, and the other client stays isolated. |

The single-line Text fixture stored 50 embedded LF values as spaces. The original
failed oracle and submitted bytes were retained, the exact observed normalization
was independently checked, and only the remaining steps resumed. This does not
establish multiline Text Area preservation or justify normalizing arbitrary data.

## Defects found and incorporated

- Developer Edition had been represented as a sandbox. Resolution now preserves
  actual IsSandbox and edition separately, using matching authoritative org data.
- A field capture could include an incidental parent object. Recovery now binds
  selectors to exact source files and verifies paths, hashes and companion-file
  completeness; unverifiable captures are not offered as automatic recovery.
- Current CLI bulk result shapes and failures could lose the job ID or misstate
  partial work. Shared parsing now reconciles exact jobs, statuses and counts;
  polling keeps partial application distinct and retains timeout evidence.
- Ordinary CRLF CSV input failed against the CLI default. Wrappers now specify
  the detected line ending without rewriting the input data.
- A failed browser switch could add a false restoration failure when the original
  admin was still active. Restoration checks the observed identity first. Encoded
  Setup confirmation-token redaction was also strengthened. These repairs have
  offline coverage; successful named-user Login As restoration remains unproven.

## Cleanup and privacy

All 208 owned requests and three follow-ups were reconciled with complete ALL
ROWS before/after queries and soft-deleted; the 201 previously deleted rows were
not deleted again. Comparison preserved all other observed cleanup fields; it did
not claim unqueried field coverage. No hard purge occurred.

The three owned permission assignments were removed. The synthetic user remains
inactive with its original identity and Minimum Access profile. Fresh independent
queries confirmed all seven nonfixture grants unchanged and license usage back to
the original two of four. Both objects and their fields, both permission sets, tabs
and layouts, and the Flow definition and all versions are absent from active
metadata. The exact synthetic Error interview was captured and ordinary-deleted.

Failed cleanup attempts remain in the evidence. Version-qualified Flow deletion
resolved the unversioned-delete limitation after the owned Error interview was
identified using both 15- and 18-character string values. Direct deletion of the
objects' sole layouts failed and rolled back; fresh reads established that all six
attempted access/UI components still existed. The revised jobs removed the two
permission sets and tabs first, then each object; final listings established layout
absence. Reports alone did not establish cascade completion.

Earlier bounded privacy scans covered public source, private evidence, recursively
parsed JSON, decoded strings and visual review of the four browser images. One
private response cookie and encoded Setup token diagnostics were redacted with
audit records retaining the original and redacted hashes. Credential masking is not general
PII anonymization. The companion final artifact report records the closing delta
scan and exact candidate hashes; private raw evidence is retained separately.

No normal-query absence, soft delete or metadata deletion establishes physical
erasure from provider storage, logs or backups. No authentication-policy change
was deployed.

## Remaining qualification

Non-admin browser Login As, business UI interaction, negative FLS and positive FLS
remain blocked by the org's disabled administrator impersonation setting. No
permission to enable that org-wide setting was supplied. A prepared check-only
candidate succeeded; the actual setting remained disabled. No browser business-record mutation, auth-policy change or successful named-user
restoration is claimed.

Also unproven: production-scale load, distributed concurrency, all editions and
browsers, third-party managed-package combinations, provider-specific meeting/
vision/prompt behavior, live Apex probe compilation, accessibility, external-user
onboarding and comparative benchmarks. Bulk restore remains manual. Captures are
operation-specific evidence, not complete org backups.

The appropriate description is an unpublished alpha with observed strengths in
consulting continuity and bounded Salesforce delivery. Industry leadership,
production readiness and client approval need their own evidence.
