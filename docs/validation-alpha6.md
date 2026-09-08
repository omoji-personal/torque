# Validation through alpha 6 — September 7, 2026

This is an unpublished development alpha with bounded Salesforce acceptance
evidence. A lower reasoning effort agent executed the test phases. A separate AI
agent audited plans, fixtures, scripts and repairs; the primary agent judged raw
reports, captures and independently queried state. Failed attempts remain in the
private evidence record. No remote CI, public publication, client approval,
production certification or comparative performance result is implied.

## What ran on each version

| Version | Observed work |
| --- | --- |
| Alpha 4 | Fixture validation/deployment; positive, negative and repeated-edit Flow business cases through the admin API; permission configuration; scoped data recovery; exact-job/evidence/continuation checks. |
| Alpha 5 | Repaired metadata recovery preserving later parent/sibling edits; CRLF bulk import, complete delete capture across 201 records, failed-capture protection, partial application; owned fixture cleanup. |
| Alpha 6 | Pure deployment-result consistency repair, 46 new regressions, full source regression, retained-report compatibility and final installed-artifact checks. The org mutations above were not repeated on alpha 6. |

The installed alpha 5 runtime stayed unchanged while alpha 6 source was prepared.
Private cleanup fixtures and scripts were revised through separately audited steps. Alpha 6 rejects supplied failure details or contradictory test counts
even when a deployment summary says Succeeded. Missing optional summary details
remain supported. This parser repair neither submits nor retries a Salesforce job.

## Source and artifact evidence

- 766 pytest tests and 76 subtests passed on macOS/Python 3.14.3. All 12 inherited
  standalone suites produced nonempty measured completion. Five optional
  source-mirror checks within the QA standalone suite remain skipped.
- The offline harness intercepted 17 Salesforce failure-path requests using its
  unavailable-tool stub; none were live provider requests.
- Provenance checks cover 178 inherited files with original source hashes retained.
  All 49 recipe adapters, 42 original mappings and 64 bundled resources agree.
- The three newly reproduced contradictory deployment responses changed from
  false completion to partial evidence while retaining the exact job ID. Twelve
  retained actual deployment reports kept their classification and identity.
- The final artifact record supplied with the candidate identifies the exact wheel
  and source-archive hashes, package/source correspondence, archive exclusions,
  dependency check, fresh core installation and installed smoke/replay results.
  Use that record to identify the qualified bytes; source tests alone do not
  qualify an arbitrary rebuilt artifact.

Installed help/import coverage and actual local workflow execution are different
claims. The smoke exercise covers help/import paths for nine delegates and six
public routes, plus the no-org demo, client/change continuity, failed-check history
and customized workflow updates in fresh processes. It does not prove live use of
every optional delegate or operation by outside practitioners.

## Live acceptance

The environment was one explicitly designated disposable Developer Edition org,
Salesforce CLI 2.150.6 and the connected administrator. Its actual IsSandbox value
was false; Developer Edition and sandbox status are represented separately.

The [alpha 5 record](validation-alpha5.md) explains each bounded result and repair.
The principal observations were:

- Exact failed and successful check-only metadata jobs, followed by a separate
  actual deployment and observed active Flow version.
- Correct first-Urgent and created-Urgent follow-ups, stable identity/count through
  repeated edits and sequential re-entry, restricted-picklist rejection and a
  deliberate downstream failure with transaction rollback.
- Scoped data and metadata recovery preserving unrelated later edits; exact job
  and component identity, before-state captures and fresh readback.
- 201-row bulk import/delete with exact ID reconciliation, all described fields
  inventoried and 12 independently queried before-values compared. Two timestamp
  fields had inventory coverage only. Text-field newline normalization is recorded;
  no multiline Text Area preservation claim is made.
- Incomplete capture prevented underlying job submission. A three-row partial
  update retained two exact successes and one failure, with stored values matching
  and Torque reporting applied_partial rather than complete.
- API permission observations, fresh-process continuation, retained failed checks,
  captured-evidence integrity and another client's isolation.

Active fixture cleanup is complete. Independently queried state confirmed both
objects and fields, permission sets, tabs, layouts and the Flow definition/versions
absent. All seven nonfixture grants were preserved, the synthetic user is inactive
with its original profile, and license usage returned to the baseline two of four.
Before metadata removal, complete ALL ROWS queries reconciled 208 soft-deleted
requests and three follow-ups. The owned Error interview was captured and
ordinary-deleted. No hard purge or org-wide authentication-policy change occurred.

The original 27-case matrix includes separate environment, business, recovery,
continuity, cleanup and privacy judgments. The companion final test report retains
each case result and the closing privacy delta. Scans are bounded, point-in-time
checks, not general anonymization or proof of physical erasure. Private raw
responses and failed attempts remain outside the public source/artifacts.

## Qualification still required

Four planned non-admin browser cells remain blocked: Login As/restoration, actual
business UI use, negative FLS and positive FLS. The org-wide impersonation setting
was disabled. A check-only settings candidate was prepared, but no authorization
to change that setting was supplied and no actual change was made. API permission
observations are not a substitute for those UI results.

Remote CI is configured for macOS/Linux and Python 3.10, 3.12 and 3.14; those remote
jobs have not run for this continuation. Other open evidence includes a second
complete consulting journey, outside-user onboarding, two-host assistant usability,
existing delivery-stack compatibility and fair comparative benchmarks. Optional
meeting/vision/prompt providers, live Apex probe compilation, accessibility,
production-scale load and distributed concurrency remain unqualified. Bulk restore
is manual; operation captures are not full org backups.

The [product direction](product-direction.md), [benchmark protocol](benchmark-protocol.md)
and [client adoption guide](client-adoption.md) describe the next evidence to collect.
They do not impose runtime approval rituals or establish provider/client approval.

## Reproduce local qualification

Install a regular checkout into an isolated environment with the development
dependencies. Use fresh output directories for each candidate:

```sh
python workflows/sync_adapters.py --check
python scripts/sync-workflows.py --check
python scripts/check-provenance.py
python scripts/test-offline.py -q
python -m build --outdir work/release-dist
python scripts/check-distribution.py work/release-dist/*.whl --sdist work/release-dist/*.tar.gz
python -m venv work/wheel-venv
work/wheel-venv/bin/python -m pip install work/release-dist/*.whl
work/wheel-venv/bin/python -m pip check
work/wheel-venv/bin/python scripts/smoke-installed.py --require-wheel
```

The [live protocol](live-acceptance.md) requires an explicitly designated disposable
org and separate private evidence. Preserve exact failed or pending jobs and
inspect their state before deciding a next action; do not rerun mutations merely
because their output or wrapper was incomplete.
