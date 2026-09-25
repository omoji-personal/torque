# Validation for alpha 17

Alpha 17 adds two knowledge skills, `salesforce-npsp` and `salesforce-nonprofit-cloud`,
and lets a skill ship `references/*.md` files. No command, gate or runtime behavior
changed, so the [alpha 16 record](validation-alpha16.md) still covers the runtime.

## Review scope

Offline: the full suite on macOS with Python 3.14, the public hygiene tests including
the private name check, and `scripts/sync-workflows.py --check`. The skill content was
checked against Salesforce documentation, the NPSP source and developer-org
observations; claims that could not be confirmed are marked "verify" in the skills. The
CI matrix and a live check of the skills against NPSP and Nonprofit Cloud orgs are
recorded here when they run.

## Suite results

`python scripts/test-offline.py -q` on 2026-09-25 (macOS, Python 3.14.7): 3343 passed,
9 skipped, 154 subtests passed (alpha 16's baseline on the same machine: 3329 passed,
9 skipped). The skips are the same nine alpha 16 lists. With plain pytest and the private
denylist configured, `tests/test_public_hygiene.py` passes. A wheel built from this tree
passes `scripts/check-distribution.py` and, installed in a fresh virtual environment,
`scripts/smoke-installed.py --require-wheel`.

## Tests

`tests/test_skills.py` checks every packaged skill's frontmatter, that each nonprofit
skill links every reference file it ships, that the bundled copy matches
`.agents/skills/`, that each bundled file matches the wheel's package data and the
source distribution manifest, that `torque workspace init` installs the skills and
references in both skill folders and `workspace upgrade` manages them, that the skills
are listed in the docs, and the release records. `scripts/smoke-installed.py` now
expects five skills.

## Edited a16 tests

Release records only, edited the way alpha 16 edited alpha 15's. In
`tests/test_docs_delegated.py`, `test_version_is_alpha16` becomes
`test_version_is_alpha16_or_later`, and `test_changelog_readme_and_record_for_alpha16`
reads the 2.0.0a16 changelog section wherever it sits and checks that the README names
the current version.
