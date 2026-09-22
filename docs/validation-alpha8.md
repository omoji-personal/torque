# Validation through alpha 8 — September 8, 2026

This is a development alpha with a public continuation branch; no PyPI release
has been published. Alpha 8 binds Metadata API receipts to
the selected org and accepts equivalent, checksum-validated 15/18-character job
IDs. Lower-effort agents ran the tests; separate agents audited the changes, and
the primary agent reviewed the implementation, original results and source hashes.
[Alpha 7 validation](validation-alpha7.md) retains the previous candidate's record.

## What changed

- Metadata reports are fetched directly from the selected org's REST endpoint
  through Salesforce CLI. The CLI deployment cache cannot choose another org for
  this request. Existing QA arguments and receipt structure remain available.
- Both requested and returned deployment IDs are validated before comparison.
  Canonical 15-character and checksum-verified 18-character forms can identify the
  same job; raw IDs remain in the receipt. Different jobs and malformed checksums
  cannot pass by truncation or case folding.
- CLI and HTTP retrieval success are checked separately from the deployment's
  outcome. A successfully retrieved failed validation still reports FAIL.
  Transport failures remain ERROR, with deployment outcome undetermined.
- HTTP headers, cookies, unrelated transport fields and stderr are excluded from
  retained deployment evidence. Full normalized deployment results remain private.

The transport uses the documented versioned Metadata REST status endpoint. It
keeps Salesforce authentication in the existing CLI; no service account, backend
migration, cache clearing or new approval ceremony is required. The current live
qualification used Salesforce CLI 2.150.6. [Salesforce deployment status API](https://developer.salesforce.com/docs/atlas.en-us.api_meta.meta/api_meta/meta_rest_deploy_checkstatus.htm)

## Source regression

| Local runtime | Pytest tests | Subtests | Standalone suite completions | Stubbed Salesforce failure paths |
| --- | ---: | ---: | ---: | ---: |
| macOS, Python 3.10.21 | 858 | 154 | 12 | 16 |
| macOS, Python 3.12.14 | 858 | 154 | 12 | 16 |
| macOS, Python 3.14.3 | 858 | 154 | 12 | 17 |

All three source runs passed. Actual imports resolved to canonical source;
427 public source files matched the frozen inventory before and after the matrix.
The same cases ran on three runtimes; these are not 2,574 different tests.
Subsequent validation-document updates do not change the tested runtime or tests.
Five inherited optional source-mirror checks within the QA standalone harness
remain skipped. No provider call was made by the offline matrix.

Fourteen new test methods cover cached-org false attribution, valid ID forms,
wrong jobs, invalid checksums, transport failures, nested failed outcomes and
header exclusion. The current-source counterexample reproduced a false PASS;
the corrected transport rejects that synthetic cross-org case.

Provenance now covers 180 files and retains inherited source hashes. All 49 recipe
adapters, 42 original mappings and 64 bundled resources must continue to agree.
Exact distribution bytes, clean installation, dependency checks and installed CLI
results belong to the companion candidate verification record. Source tests do
not qualify an arbitrary rebuilt artifact.

## Completed live Apex qualification

The previously prepared pure-Apex sequence completed in one explicitly authorized
Salesforce Developer Edition org. These executions used the frozen alpha 7
candidate; the Apex generator is unchanged in alpha 8.

| Observation | Actual result |
| --- | --- |
| Deliberately invalid source, check-only | Expected missing-return compiler rejection; no fixture created |
| Three-class draft, check-only | Compiled; fresh query confirmed no persistent fixture classes |
| Three-class actual deployment | Successful; exact created IDs and source bodies verified |
| Unresolved generated draft | 11 exact expected DRAFT assertion failures |
| Reviewed assertion update | Exactly one test class updated; IDs and other class bodies preserved |
| Adapted generated tests | The same 11 methods passed |
| Independent authored contract | Five selected methods passed |
| Intentional wrong expectation | One exact expected assertion failure |
| Cleanup validation and deletion | Exactly three owned classes removed; fresh four-name query found none |

These are 28 method observations across four test jobs: 16 passes and 12 deliberate
assertion failures. Compiler validation is separate from test execution. The
fixture code uses no record DML, SOQL, callouts, user creation or permission changes.
Contract checks include null/empty inputs, Unicode, typed overloads, an instance
method, Boolean/Decimal behavior, collection boundaries through 251 elements,
nested collections and input preservation. This does not establish FLS, sharing,
managed-package behavior or general business acceptance.

Three anticipated provider-shape differences were corrected in the private test
harness: successful report retrieval of a failed deployment, omission of one final
source newline, and equivalent 15/18-character test-job IDs. Original failed
harness attempts and complete results were preserved. Each correction received
regression tests and independent review; no job was resubmitted. At closure all
963 sealed inputs remained unchanged, and owned-fixture cleanup was complete.

## Live receipt and host observations

Read-only alpha 8 source checks against the existing deployment records observed
PASS for the successful deployment in both valid ID forms, and FAIL for the
expected failed validation. Each request used the selected org's endpoint and
HTTP 200 response. No deployment was repeated to obtain these reports. A live
cross-org negative was not attempted; its counterexample remains an offline test.

A Mac restart restored access to the existing saved Salesforce connection, after
an earlier Keychain access failure. No Keychain policy, credential backend or org
authorization was changed. This establishes current access, not a diagnosis of the
original host failure or proof of future locked-screen/credential-renewal behavior.

## Remaining evidence

The prior 27-case live matrix remains 23 passed and four blocked non-admin browser
cases, as recorded in [alpha 6](validation-alpha6.md) and [alpha 5](validation-alpha5.md).
Those journeys were not repeated here, and no org-wide Login As policy was changed.

GitHub Actions passed all six Ubuntu/macOS jobs on Python 3.10, 3.12 and 3.14
for commit `6db2af4998461c513c4872874eaa3c68df50eb18`. Each job passed 858 tests,
154 subtests, 12 standalone suites, distribution checks and the installed-wheel
smoke checks. These independently built CI artifacts are separate from the local
candidate's exact bytes. [Verified CI run](https://github.com/omoji-personal/torque/actions/runs/34240338628)

Windows remains unqualified. Non-admin browser journeys, outside-user onboarding, complete
consulting journeys across assistant hosts, existing delivery-stack compatibility
and comparative benchmarks remain open. Optional provider behavior, accessibility
and production-scale load remain unqualified. Bulk restore is manual; operation
captures are not full org backups. No public release or industry-leadership claim
is established by these local results.

The [product direction](product-direction.md), [benchmark protocol](benchmark-protocol.md)
and [client adoption guide](client-adoption.md) describe additional evidence.
They do not impose runtime approval rituals or establish provider/client approval.

## Reproduce local qualification

Use Python 3.10+ with development dependencies. Install current source or select
its canonical package roots; verify imports if another Torque version is installed.

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

Use a new output directory for each candidate. Installed qualification runs outside
the checkout with inherited PYTHONPATH removed. See the [live protocol](live-acceptance.md)
for separately scoped disposable-org tests.
