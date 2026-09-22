# Contributing

Build for working Salesforce consultants: reduce repeated work, retain useful
JSC capabilities and make outcomes understandable. Start with a concrete user
problem and a small reproducible example. The continuation currently has no active
external users; compatibility decisions should still be explicit in the changelog.

Use synthetic fixtures. Keep org auth, client exports, private workspace content,
recordings, screenshots with personal data and recovery payloads out of source,
issues and pull requests. A sanitized reproduction should contain only what is
needed to reproduce the problem.

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
No global sf interception, ambient authorization tokens or mandatory administrative
workflow should be introduced by default. Proposed integration with another tool
should identify what Torque adds to the user's existing workflow.

Useful research should produce a concrete improvement or an explicit integration
decision. Record material findings in [research adoption](docs/research-adoption.md)
with the affected surface and evidence still needed. This is a lightweight
maintainer reference, not a required record for every observation or client task.

There is currently no promised support SLA. Use repository issues for sanitized
bugs or product proposals. Security-sensitive material follows SECURITY.md.
