# Alpha 2 validation — 2026-09-07

The continuation was validated on macOS with Python 3.14.3. Offline regression
and packaging checks are listed separately from the bounded live Salesforce
exercise below. Browser and model-provider workflows remain untested live.

| Check | Result |
|---|---|
| Pytest and unittest collection | 387 passed; 76 subtests passed |
| Standalone inherited fixture suites | All 12 exited successfully |
| Original conversational command coverage | All 42 names mapped, with 7 additional recipes |
| Recipe/Claude adapter synchronization | All 49 match |
| Bundled conversational resources | 64 match: catalogue, 49 commands, 5 rules, 3 skills, 6 agents |
| Review skills | All 3 pass the skill frontmatter/structure validator |
| CLI example parser review | 52 cases checked without invoking handlers |
| Installed CLI smoke | Nine delegate help routes, 49 recipes, private initialization, two-client isolation, session journal and handoff pass |

The offline runner separates Python test cases from legacy executable fixture
harnesses so a module containing only `main()` is actually run. Temporary client
state is used throughout. Requests intentionally exercising an unavailable
Salesforce backend are served by a local stub; no live Salesforce call is made.
The source-specific managed-package examples reported as skipped by the inherited
QA harness are not counted as live or client coverage. Counts above are kept
separate because these harnesses use different assertion/reporting conventions.

## Reproduce

Install the checkout with the development test dependencies, then run:

```sh
python workflows/sync_adapters.py --check
python scripts/sync-workflows.py --check
python scripts/test-offline.py -q
python -m build
python scripts/check-distribution.py dist/*.whl --sdist dist/*.tar.gz
python -m venv work/wheel-venv
work/wheel-venv/bin/python -m pip install dist/*.whl
work/wheel-venv/bin/python scripts/smoke-installed.py --require-wheel
```

Use an empty `dist/` output location when testing a new version. The smoke test
changes to a fresh temporary directory and removes `PYTHONPATH`; a wheel check
requires imports to resolve from `site-packages`. The clean environment installs
only the core dependencies. Optional Playwright/Pillow imports must not be needed
for ordinary CLI help, context, or handoff use.

The CI workflow is configured for Python 3.10, 3.12 and 3.14 on Linux. Remote CI
has not been run or claimed as passing during this local implementation.

## Corrections exercised

- Client state and lessons are selected at call time rather than from cached
  global paths or the current directory. Stale adapters are cleared when changing
  clients; symlink escapes from selected state/config paths are rejected.
- Session records are created atomically, preserve prior entries, and identify
  writer-supplied status. An evidence hash proves the referenced bytes at capture,
  not the truth of the recorded result.
- Browser replay scanners identify category/location without echoing matched
  credentials. Empty or entirely skipped browser coverage and unknown cleanup
  no longer produce a successful completion result.
- QA log analysis interprets findings and available-log coverage; process exit
  alone is not treated as a clean outcome. Advisory evidence distinguishes an
  intended user's assignment from a permission set having any assignee.
- Recovery uses the explicit target and matching interpreter. Existing limits,
  including some null/quoted value restoration and nonreversible operations, are
  reported rather than generalized into a claim of complete undo.

## Live API acceptance

A user-selected disposable Developer Edition org was exercised using Salesforce
CLI 2.150.6 and API 67.0, one private Torque client, and unique synthetic metadata.
The operator was the connected System Administrator; this is not non-admin UAT.
The sequence began with alpha 1 and continued with the alpha 2 fixes. The final
create/error and cleanup checks used alpha 2. All evidence remains private. Eight exact metadata jobs (validation, deployment,
field update/recovery, and validated cleanup) reached terminal success with zero
component errors. Cleanup establishes absence from active use, not permanent
purging of Salesforce recycle-bin data.

| Exercise | Observed result |
|---|---|
| Validation | Exact job succeeded, all 3 intended components matched, checkOnly=true, object still absent |
| Deployment | Separate exact job succeeded with checkOnly=false; live metadata present |
| Metadata recovery | Captured prior field label and hashes; changed label, restored it, verified live label and parent snapshot |
| Assignment | No assignment before; one exact user/permission-set assignment afterward; missing CLI assignment IDs reported honestly |
| Record recovery | Created row, changed value, made separate later Name edit; recovery restored value and preserved later Name |
| Create diagnostic | Invalid field produced visible error code and evidence path; no row created |
| Successful create evidence | Valid record ID and actual after-row captured; no retry during evidence capture |
| Cleanup | Two synthetic records absent; exact assignment and permission set removed; custom object removed from active metadata |

The first create attempt failed because the new field was not accessible before
permission-set assignment; the wrapper exposed no error details. Alpha 2 corrects
that diagnostic gap. Code inspection also found the whole-row data restore bug
and assignment-response parser crash; both were fixed before their live execution.
One recovery QA invocation overclaimed an unchanged parent container and correctly
failed component matching. The corrected field-only assertion passed for the
same exact job. A successful narrower report does not erase the original failure.

See [the repeatable exercise](live-acceptance.md) for commands, scope, and recovery
limits. In particular, metadata pre-state is captured with `--metadata`, not with
`--source-dir` or `--manifest`; newly created metadata cleanup is explicit.

## Still to exercise live

A representative sandbox engagement must test meaningful Flow/business outcomes,
non-admin browser behavior, and end-to-end client handoff. Browser setup,
passkey/MFA handling, actual session/user identity, Login As restoration, and
browser-created test-record cleanup need live evidence. Browser profile labels and successful navigation do not independently
prove the intended user's effective access.

Optional Gemini adapters in meeting analysis and prompt regression require a
separate provider exercise with appropriate input material. The prompt harness
checks configured output contracts; it does not prove production Prompt Builder
behavior or the quality of the business interpretation.

Accessibility and visual-regression QA surfaces that remain deferred in the
inherited dispatcher are not implemented by this release. Generated Apex probes
still need business assertions and real compilation/execution. Employer adoption,
public publication, remote CI, and any production rollout are separate work.
