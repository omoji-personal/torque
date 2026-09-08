# Alpha 4 workflow update and validation — 2026-09-07

Alpha 4 updates eight packaged recipes and the shared tools rule from the research
adoption review. Execution code is unchanged from alpha 3 apart from the version
identifier. Live observations below were performed on alpha 3 and were not rerun
for this instruction update.

This record separates current local tests, installed artifact checks, prior live
acceptance and capabilities still needing proof. Remote CI has not run for the
uncommitted continuation. No public release or deployment is implied.

## Current offline and integration evidence

| Check | Observed result |
| --- | --- |
| Python test collection on macOS / Python 3.14.3 | 560 passed; 76 subtests passed |
| Inherited executable fixture suites | All 12 completed with nonempty success summaries |
| Original conversational mappings | All 42 preserved in 49 recipes |
| Recipe adapters / installed resources | 49 adapters and 64 bundled resources match |
| Independent reviews | Browser, bulk/lease, evidence, context/export, template update, harness and public doc/CLI review completed; concrete reproduced defects addressed |
| Documentation interface check | 32 help/version routes, 7 representative parser cases and relative links checked; parsing is not live operation proof |
| Clean installed wheel | See the artifact verification record supplied with the candidate; run the reproduction below for each exact artifact |

Core tests include two-client isolation, concurrent event writers, captured-file
drift, failed check history, metadata/business evidence distinctions, context
continuation, sibling-client export refusal, source/installed package detection,
local-edit preservation and interrupted/concurrent template updates. Browser tests
exercise identity failures, restoration, empty/skipped/backend-only coverage,
credential diagnostics and encoded artifact layouts. Bulk tests exercise described
custom fields, incomplete capture and lost leases.

The offline runner uses temporary state and unavailable external-tool stubs. Its
14 failure-path Salesforce requests were handled by the stub, not a live org.
It now rejects executable suites that decline all work, time out, or return zero
without a nonempty measured completion. Individual source-specific fixture skips
are not counted as live/client coverage.

## Alpha 3 live read-only evidence

Salesforce CLI 2.150.6, Playwright 1.62.0, an explicitly selected disposable
Developer Edition org, and the connected System Administrator were used.

- The installed browser smoke flow reached Lightning, observed the actual User Id
  through Aura CurrentUser.Id, matched it to the expected connected user and saved
  a screenshot. One applicable admin cell passed; no mutation or cleanup was needed.
  This proves that identity probe in this environment, not non-admin permissions,
  Login As restoration, browser business behavior or general cross-org reliability.
- `change verify-deploy` re-read one exact prior validation request, matched all
  three expected components and retained the full 2,574-byte parseable JSON report.
  It correctly identified `checkOnly=true`, retained explicit component names and
  left the manual/business acceptance criterion unpassed. This was a read of a
  historical job, not a new validation/deployment or a claim about current metadata.

The earlier [alpha 2 record](validation-alpha2.md) separately documents eight exact
metadata jobs and bounded data/recovery/cleanup acceptance. That evidence is not
relabelled as a new alpha 3 mutation run. Current bulk-capture and lease changes
have offline regression coverage; their expanded live behavior remains unqualified.

## Reproduce from a clean checkout

Install `.[dev]` into an isolated environment using a regular install. Then run:

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

Use new output/environment directories for a new artifact. The smoke check imports
from site-packages outside the checkout and uses fresh CLI processes. It covers all
nine delegates, six new public routes, the no-org demo, two clients with two changes
each, a failed check surviving resumption/handoff, and customized workflow upgrades.
It is deterministic application acceptance, not a trial with two actual assistant
hosts or outside practitioners. The fresh core wheel needs no Playwright/Pillow.

CI is configured for macOS and Linux with Python 3.10, 3.12 and 3.14, pinned action
revisions and clean wheel checks. These six remote jobs remain unrun until the
reviewed source is pushed and CI executes. Local validation currently establishes
macOS/Python 3.14 behavior; do not display remote badges as passing.

## Remaining qualification

- Representative Flow outcomes, negative and repeated-edit cases, non-admin browser
  access, Login As restoration and mutation cleanup need an end-to-end live run.
- Optional model-provider meeting/vision/prompt adapters need provider-specific
  acceptance with suitable inputs. Prompt contract checks do not establish deployed
  Prompt Builder/Agentforce behavior or interpretation quality.
- Deferred accessibility and visual-regression QA surfaces remain unimplemented.
  Generated Apex probes still need real compilation and business assertions.
- Recovery is operation-specific. Metadata pre-state currently uses explicit
  `--metadata` selectors, not source-dir/manifest inference. Some null/quoted data
  restoration and nonreversible operations remain limited. Bulk restore is manual;
  related records/files and fields invisible to the user are outside its capture claim.
- Outside-user onboarding, cross-host usability, existing-stack compatibility and
  comparison trials remain open. The benchmark protocol is prepared, not executed.

The supported development scope is useful private consulting continuity plus the
explicitly documented bounded operations. Broader “industry-leading” and routine
production-readiness claims require the above evidence. See product-direction.md
for the release sequence; no runtime approval ceremonies are added by that plan.
