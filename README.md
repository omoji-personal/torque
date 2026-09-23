# Torque

**Keep Salesforce consulting work easy to resume and hand over.**

Torque gives your conversational coding assistant a private workspace for each
client: business context, requirements, decisions, implementation workflows,
observations and a useful handoff. It continues JusticeserverClaude, which its
author has used in daily consulting for over six months.

Use your existing Salesforce tools and assistant. Torque requires no service
account and installs no global command interception or approval-token system.
`solution-lead` is an optional workspace profile for a consultant who leads
delivery across several clients; the product works with any firm or independent consultant.

**Status: 2.0.0a9, development alpha.** The local continuation has not been
published to a package index. Core workspace functions have offline acceptance
coverage; bounded live operations and experimental capabilities have separate limits in
[validation](docs/validation.md). No industry-leadership claim is made.

## Try a complete example without an org

From a checkout, use Python 3.10+ on macOS or Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/torque demo ../torque-demo
.venv/bin/torque context --workspace ../torque-demo --client synthetic-community-center
.venv/bin/torque handoff --workspace ../torque-demo --client synthetic-community-center
```

Choose a new demo directory whose parent exists. Open its `START-HERE.md` to
follow a fictional service-request change from discovery through prepared
metadata, acceptance criteria and handoff. Local XML checks are real; all live
checks remain visibly unrun. No Salesforce account, browser or model is needed.
[Walk through the demo](docs/demo.md).

For use from another editor or terminal, make the installed executable available
to that process. A virtual environment activated in one shell does not configure
a separately launched app. See [installation](docs/installation.md) for isolated
installation, PATH setup, optional dependencies and troubleshooting.

## Continue real client work

```sh
torque workspace init ../torque-private --name "My consulting workspace"
torque client add sample --workspace ../torque-private --org sample-sandbox
torque context --workspace ../torque-private --client sample
```

These commands create local files. An alias refers to existing Salesforce CLI
authentication; it does not log in or authorize an org. Replace the sample names.
Open the private workspace in your assistant; `AGENTS.md` and `CLAUDE.md` explain
how to use its client context and bundled workflows.

Ask in ordinary language:

- “Resume sample's contact-preference change and continue the unresolved checks.”
- “Review this Flow, implement the requested change, and validate it in sample-sandbox.”
- “Prepare a data migration with a recovery plan.”
- “Turn these discovery notes into requirements and a handoff another consultant can use.”

An optional `torque change` record connects an outcome, acceptance criteria,
decisions, reported checks and exact deployment observations. The assistant can
maintain it during work. There is no mandatory lifecycle or form to complete.
[Use engagement records](docs/engagement-records.md).

## What is included

| Work | Interface |
| --- | --- |
| Discovery, architecture, Flow review, migration planning, training, release notes | 49 conversational recipes, including all 42 original JSC command mappings |
| Resume and hand over work | Private client context, append-only session/change records, captured evidence and Markdown/JSON handoffs |
| Org and metadata investigation | `torque advisory`; use current official Salesforce CLI, skills and MCP tools alongside it |
| Deploy, data operations and scoped recovery | `torque deploy`, `torque data`, `torque org`, `torque recover` |
| QA, debug logs, browser flows, lessons and probes | `torque qa`, `logs`, `browser`, `lesson`, `probes`; coverage and limits depend on the configured adapter |
| Meeting preparation and prompt contracts | Optional `torque meeting` and `ai-regression`; live model-provider behavior remains experimental |

Run `torque --help`, `torque workflows list`, or a route's `--help`. Stateful
operations accept `--workspace PATH --client NAME` and retain their explicit org
arguments. The legacy `torque revert ...` grammar remains available for existing
scripts. Recovery is scoped; it is not a universal undo or Salesforce backup.

`torque doctor --for salesforce` checks local dependencies without contacting an
org. After installing a newer version, `torque workspace upgrade PATH --check`
previews workflow updates; applying them preserves local customizations.
[Workspace upgrades](docs/workspace-upgrades.md).

## Product direction

Torque should earn its place by reducing repeated investigation, missed
acceptance checks and handoff effort. Platform skills, AI assistance, deployment
pipelines and business traceability already exist elsewhere. We compare against
them and integrate where appropriate; command count is not a competitive claim.

- [Current tools and product comparison](docs/competitive-landscape.md)
- [How useful findings are incorporated](docs/research-adoption.md)
- [Product direction and release work](docs/product-direction.md)
- [Comparison protocol](docs/benchmark-protocol.md)
- [Validation and limitations](docs/validation.md)
- [Client adoption and provider data boundaries](docs/client-adoption.md), [optional engagement worksheet](examples/client-data-boundary.md)
- [Contributing](CONTRIBUTING.md), [security and data boundaries](SECURITY.md), [changelog](CHANGELOG.md)
- [JSC continuity](docs/continuation.md), [workflow mapping](docs/workflow-continuity.md), [package provenance](docs/package-migration.md)

Client data, credentials, org mappings and employer documents belong in private
workspaces, outside the public source. Git ignore rules do not untrack files
already committed. The assistant and any external tools retain their own data
handling and account requirements. Torque itself has no hosted backend or
embedded telemetry service.
