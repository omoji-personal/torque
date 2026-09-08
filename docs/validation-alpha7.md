# Validation through alpha 7 — September 7, 2026

This is an unpublished development alpha. The current cycle fixes practical client
continuity and false-positive Apex draft QA. A lower reasoning effort agent ran the
checks, a separate agent audited the changes, and the primary agent reviewed the
implementation, original reproductions, raw results and exact source hashes.

## What changed

- New clients are completely staged before publication. Interrupted creation can
  be retried; cooperating concurrent creators cannot replace an existing client.
- Client handoffs include the same bounded firm and selected-client notes used for
  context resumption. Unicode limits are stated in characters. Existing journals,
  changes and evidence remain included; sibling-client notes remain excluded.
- Explicit workspace selection reads preserved local workflow customizations.
  Missing local recipes use the packaged fallback; catalogue JSON stays stable.
- Apex drafts now call the supported target methods with typed arguments and
  intentionally unresolved business assertions. Comments, inner bodies and unrelated
  declarations cannot become target calls. Unsupported methods stay visible.
  Edited outputs are preserved, source API versions are honored, and long generated
  names are shortened deterministically. Deliberately excluded inner-class scope
  gets an informational note without an unnecessary extra review test.
- QA reports generated drafts as uncompiled/unexecuted work requiring completion,
  including truncated or incomplete generation. Invented bulk/FLS workloads and
  catch-and-log success paths were removed.

## Source regression

| Local runtime | Pytest tests | Subtests | Standalone suite completions | Stubbed Salesforce failure paths |
| --- | ---: | ---: | ---: | ---: |
| macOS, Python 3.10.21 | 844 | 76 | 12 | 16 |
| macOS, Python 3.12.14 | 844 | 76 | 12 | 16 |
| macOS, Python 3.14.3 | 844 | 76 | 12 | 17 |

All three source runs passed. Exact imports resolved to the canonical source
packages; 405 runtime, test, workflow and configuration files matched the frozen
hash inventory before and after every run. These are the same cases across three
runtimes, not 2,532 distinct tests. Five inherited optional source-mirror checks
inside the QA standalone harness remain skipped. No provider call was made.

The source suite gained 78 pytest cases since alpha 6. Focused checks also retain
process-termination, concurrent publication, Unicode truncation, another client's
isolation, local customization, parser/overload, edited-output preservation and
QA false-success reproductions. The strengthened installed-workflow check failed
against alpha 6 at its missing-handoff-notes assertion, as expected.

Provenance still covers 178 inherited files, retaining original source hashes.
All 49 recipe adapters, 42 original mappings and 64 bundled resources agree.
Final artifact byte correspondence, regular installation, dependency checks and
installed CLI results belong to the companion candidate verification record;
source tests alone do not qualify an arbitrary rebuilt artifact.

## Live qualification boundary

The prior live work and completed fixture cleanup remain documented in
[alpha 6 validation](validation-alpha6.md) and [alpha 5 validation](validation-alpha5.md).
Those operations were performed on earlier versions and were not repeated for
this update. The existing 27-case live matrix remains 23 passed and four blocked
non-admin browser cases. No org-wide Login As policy was changed.

This cycle's fresh authentication preflight did not reach Salesforce: the saved
account could not be opened because macOS Keychain refused credential access.
Unlocking or reconnecting the designated disposable org is required for new live
compiler/test observations. No new class deployment or org cleanup was attempted.

A private pure-Apex fixture is prepared and independently reviewed: exactly three
classes, 11 generated draft cases, the same 11 cases with only their business
assertions supplied, five authored positive contract methods and a separate
intentional wrong-expectation control. The planned sequence distinguishes invalid
check-only compilation, draft failures, adapted-case success, independent contract
results and owned-class cleanup. It uses no persistent record DML, SOQL, callouts,
users or permission changes in the class code. Prepared source and static review
are not compiler or test-run evidence. The exact live executor still needs review
against restored access before execution.

## Remaining evidence

Remote macOS/Linux CI has not run for this continuation; Windows remains
unqualified. Live Apex compilation, the four blocked non-admin browser cases,
outside-user onboarding, complete consulting journeys across assistant hosts,
existing delivery-stack compatibility and fair comparative benchmarks remain open.
Optional provider behavior, accessibility and production-scale load remain
unqualified. Bulk restore is manual; operation captures are not full org backups.

The [product direction](product-direction.md), [benchmark protocol](benchmark-protocol.md)
and [client adoption guide](client-adoption.md) describe additional evidence.
They do not impose runtime approval rituals or establish provider/client approval.

## Reproduce local qualification

Use Python 3.10+ in an isolated environment with development dependencies. Install
current source or explicitly select all canonical package roots for source tests;
verify actual imports when another Torque version is already installed.

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

Use a new output directory for each candidate. Installed qualification must run
outside the checkout with inherited PYTHONPATH removed. See the
[live protocol](live-acceptance.md) for separately scoped disposable-org tests.
