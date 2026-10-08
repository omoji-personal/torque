# Contributing

Build for working Salesforce consultants: reduce repeated work, retain useful
inherited capabilities and make outcomes understandable. Start with a concrete user
problem and a small reproducible example. The continuation currently has no active
external users; compatibility decisions should still be explicit in the changelog.

Use synthetic fixtures. Keep org auth, client exports, private workspace content,
recordings, screenshots with personal data and recovery payloads out of source,
issues and pull requests. A sanitized reproduction should contain only what is
needed to reproduce the problem.

## Public naming and attribution

Keep private firm, client and personal material out of tracked files and release
artifacts. The tree names no earlier employer or product. Describe inherited work
in neutral terms such as "the earlier toolkit"; the provenance manifest records its
source repository under the neutral label `prior-toolkit`. Legacy identifiers such
as the `jsc_*` package names, the `jsc` console commands and the `JSC_*` environment
variables are kept for compatibility. They are technical names that programs depend
on, not branding, and product introductions need none.

This does not prohibit necessary public authorship, LICENSE/NOTICE text or dependency
attribution, including the Apache-2.0 notice for the modules derived from
claudeblazer. Retain required notices and the original provenance hashes. Fictional
data uses reserved example domains such as `example.org` and `example.com` (reserved
`.example`, `.invalid` and `.test` fixtures are also valid).

Names that must not appear are checked through a private denylist kept outside the
repository, so no tracked file spells or encodes one. Set `TORQUE_PRIVATE_DENYLIST`
to a private file outside the checkout, one term per line with optional `#` comments;
`tests/test_public_hygiene.py` and `scripts/check-distribution.py` both apply it.
The offline launcher preserves this setting and reports whether the scan passed,
failed or did not run. Missing or empty configured files fail the scan. Without the
setting, the name check does not run.

`tests/test_public_hygiene.py` also checks tracked text for credential shapes,
personal absolute paths, internal URLs and fixture email domains.
`scripts/check-distribution.py` scans wheel and source-distribution member contents
for the same credential/path/URL shapes. Public author metadata and required notices
are intentionally allowed. These checks cannot recognize all private names or PII.
Diagnostics show file locations without reproducing private terms or suspected
credentials.

## Development

```sh
python3 -m venv .venv
.venv/bin/python -m pip install '.[dev]'
.venv/bin/python workflows/sync_adapters.py --check
.venv/bin/python scripts/sync-workflows.py --check
.venv/bin/python scripts/test-offline.py -q
```

Use a regular install after source changes when validating installed behavior.
The offline runner selects the current checkout for pytest and child-process
imports, even if an older Torque package is installed. It runs the standalone
fixture suites as well. For a focused iteration, use
`python scripts/test-offline.py --pytest-only tests/test_workspace.py -q`;
this explicitly omits the standalone suites and is not full qualification.

The runner uses a temporary home and configuration directories, drops ambient
authentication variables, and exposes only stubbed live tools and the local tools
needed by fixtures. Windows stubs use distlib's native executable launchers from
the development dependencies. A Python startup guard follows child interpreters,
blocks external DNS/TCP/UDP and unapproved direct executables, and resolves tool
stubs before Windows can search the working directory. Numeric loopback remains
available for local fixture servers. Git network protocols and user hooks are
disabled. This is accidental-call isolation for trusted tests, not an OS sandbox
for hostile Python, native extensions or arbitrary commands inside a shell.
Use an OS network sandbox when running untrusted code.

Keep conversational recipes under `workflows/`, then run `workflows/sync_adapters.py`
and `scripts/sync-workflows.py` to refresh adapters and wheel resources. See
[validation](docs/validation.md) for the clean-wheel check. A source test does not
establish that an installed package or optional live adapter works.

Regression tests should exercise observable behavior: actual wrong-client refusal,
preserved local edits, failed/partial results, meaningful business assertions,
error paths and recovery scope. Avoid tests that merely mirror implementation or
report a green run when no tests executed. Live examples need explicit disposable
org scope and cleanup of only their own artifacts.

Pull requests should state the concrete problem, resulting behavior, validation
and remaining limits. Retain relevant provenance when adapting inherited code.
When adapting `packages/` files, run `python scripts/check-provenance.py` to identify
changed entries, update only their `destination_sha256` and `adapted` fields in
`packages/provenance.json`, and run the check again. Preserve `source_sha256`, source
paths, notices and revision records. See [package provenance](docs/package-migration.md).

No global sf interception, ambient authorization tokens or mandatory administrative
workflow should be introduced by default. Proposed integration with another tool
should identify what Torque adds to the user's existing workflow.

Useful research should produce a concrete improvement or an explicit integration
decision. Record material findings in [research adoption](docs/research-adoption.md)
with the affected surface and evidence still needed. This is a lightweight
maintainer reference, not a required record for every observation or client task.

There is currently no promised support SLA. Use repository issues for sanitized
bugs or product proposals. Security-sensitive material follows SECURITY.md.
