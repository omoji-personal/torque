# Torque

**Keep Salesforce consulting work easy to resume and hand over.**

Torque is a Python command-line tool and a set of conversational workflows that
give your coding assistant a private, local workspace for each Salesforce client:
business context, requirements, decisions, implementation workflows, observations
and a handoff another consultant can use.

**Status: development alpha, version 2.0.0a19.** It has not been published to a
package index; install it from a checkout as shown below. Core workspace functions
have offline acceptance coverage; bounded live operations and experimental
capabilities have separate limits in [validation](docs/validation.md).
**Recovery is scoped; it is not a universal undo or Salesforce backup.**

## A handoff you can resume

This synthetic example summarizes the [offline demo](docs/demo.md):

> **Before:** "The contact-preference change is partly done. Check the notes and
> ask what still needs testing."
>
> **After:** "Phone and Email values are prepared in the metadata. Local XML
> checks passed. Requiredness, page placement and the intended staff role are
> still open. Deployment and live acceptance checks have not run. Next: resolve
> those decisions, then validate in the explicitly selected sandbox."

The context and handoff commands bring together recorded decisions, evidence and
remaining work so the next consultant can see where to continue.

| Capability | Practical limit |
| --- | --- |
| Resume work from private client context, session/change records and Markdown/JSON handoffs | Recorded evidence supports review; it does not prove a supplied claim |
| Guide discovery, architecture, Flow review, migration planning, training and release notes | Workflows guide the assistant; live outcomes require separate checks |
| Investigate orgs, deploy metadata, perform data operations and prepare scoped recovery | Requires existing tools and explicit org selection; recovery coverage varies by operation |
| Run QA, log analysis, browser flows, lessons and probes | Coverage depends on the configured adapter; synthesized probes are not executed tests |
| Prepare meetings and check prompt contracts | Optional dependencies apply; live model-provider behavior remains experimental |

## Install and try the offline demo

Install the exact checkout revision you reviewed. In a fresh clone, replace
`REVIEWED_COMMIT_SHA` below with that commit's full SHA from its review or CI record;
`git rev-parse HEAD` lets you compare the selected revision before installing.
A matching wheel and hash can also be used as described in [installation](docs/installation.md).

Use Python 3.10+ on macOS, Linux or Windows. CI runs all three on
Python 3.10, 3.12 and 3.14. On Windows, use `.venv\Scripts\` in place of `.venv/bin/`
and see the [Windows installation steps](docs/installation.md#windows).

```sh
git clone https://github.com/omoji-personal/torque.git
cd torque
git checkout --detach REVIEWED_COMMIT_SHA
git rev-parse HEAD
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

The [alpha 16 record](docs/validation-alpha16.md) covers the runtime, and the
[alpha 17 record](docs/validation-alpha17.md) covers the knowledge skills added since.
The [alpha 18 record](docs/validation-alpha18.md) covers browser restoration and failure
diagnostics, and the [alpha 19 record](docs/validation-alpha19.md) covers clients and
initiatives. No industry-leadership claim is made.

## Continue real client work

```sh
torque workspace init ../torque-private --name "My consulting workspace"
torque client add sample --workspace ../torque-private --org sample-sandbox
torque context --workspace ../torque-private --client sample
```

These commands create local files. An alias refers to existing Salesforce CLI
authentication; it does not log in or authorize an org. Replace the sample names.
Open the private workspace in your assistant; its `AGENTS.md` explains how to use the
client context and bundled workflows (`CLAUDE.md` points Claude Code to it). Torque works
with any assistant that reads `AGENTS.md` and can run commands. The optional build-only
gate runs as a Claude Code hook and as an [Antigravity hook](docs/ai-access.md#wiring-the-antigravity-hook);
connected mode needs Claude Code. Antigravity reads `.agents/` only, so the workspace also
gets the rules, recipes (as skills and slash commands) and worker roles there in
[its format](docs/workspace-upgrades.md#antigravity-copies).

Ask in ordinary language:

- “Resume sample's contact-preference change and continue the unresolved checks.”
- “Review this Flow, implement the requested change, and validate it in sample-sandbox.”
- “Prepare a data migration with a recovery plan.”
- “Turn these discovery notes into requirements and a handoff another consultant can use.”

An optional `torque change` record connects an outcome, acceptance criteria,
decisions, reported checks and exact deployment observations. The assistant can
maintain it during work. There is no mandatory lifecycle or form to complete.
[Use engagement records](docs/engagement-records.md).

### Clients and initiatives

Internal work that is not for a client (adopting a tool, a workspace project) is an
initiative: `torque initiative add NAME --workspace . [--owner PERSON]` creates
`initiatives/SLUG/` with the same sessions, context, change records and handoff as a
client. Use `--initiative SLUG` in place of `--client` on `context`, `session`, `handoff`
and `change`. Initiatives never get client-only powers (org, consent, approvals,
connected mode, verify-deploy). `torque initiative list|show|set-state` manages them
(`active`, `paused` with a reason, `closed` with an outcome, `archived`, which refuses new
records until reopened). `torque engagement list --workspace . [--kind client|initiative]`
shows both kinds.
Concurrent initiative lifecycle updates retain each history entry.

Creating `.torque/maintenance` pauses core record/configuration writes, new delegated
package commands through `torque` (except help), and direct revert wrapper admission in
every workspace mode. It does not cancel work already admitted or stop every direct
package API. Let active operations finish and retain their outcome files before migrating;
removing the flag resumes admission. The workflow updater remains available. See
[maintenance boundaries](docs/engagement-records.md#maintenance).

## What is included

| Work | Interface |
| --- | --- |
| Discovery, architecture, Flow review, migration planning, training, release notes | 53 conversational workflows (guided and native), including all 42 original command mappings |
| Resume and hand over work | Private client context, append-only session/change records, captured evidence and Markdown/JSON handoffs |
| Org and metadata investigation | `torque advisory`; use current official Salesforce CLI, skills and MCP tools alongside it |
| Platform knowledge | Five [skills](docs/skills.md): architecture, Code Analyzer and SOQL review, plus NPSP (with PMM and Outbound Funds) and Nonprofit Cloud, including NPSP to Nonprofit Cloud migration |
| Deploy, data operations and scoped recovery | `torque deploy`, `torque data`, `torque org`, `torque recover` |
| QA, debug logs, browser flows, lessons and probes | `torque qa`, `logs`, `browser`, `lesson`, `probes`; coverage and limits depend on the configured adapter |
| Meeting preparation and prompt contracts | Optional `torque meeting` and `ai-regression`; live model-provider behavior remains experimental |

## Optional workspace controls

Use your existing Salesforce tools and assistant. Torque requires no service
account and installs no global command interception or approval-token system by
default. A firm that wants an AI session to work in client orgs under control can opt a
workspace into [connected mode](docs/connected-approval.md): the session is bound to one
client and checks recognized org writes against approval of the exact command from the
consultant's own terminal. **Tier 1 (the default) protects against accidental actions.**
The agent shares the signing account and can bypass local approval and consumption
records through code it runs; exact-command checks are not a security boundary against
that account. With a
[delegated approver](docs/delegated-approver.md), an independent approver account (a person
or an automated reviewer) grants non-production writes instead, so a session can run
unattended, and every decision records who made it. The
catalogue's `qa-token-*` entries only manage legacy QA skip records kept for compatibility;
no workflow depends on them.

Extensible tools such as Git, linters and search tools with preprocessors require review
in connected mode. Run untrusted project tooling in an isolated account or container
without Salesforce credentials;
Torque does not create that isolation. External browser and desktop reads are refused
because their current org context cannot be verified. Torque's isolated browser requires
an org window and consent for both metadata and record data.

Stronger enforcement requires signing, consumption state and execution authorization
outside the agent account, with grants bound to an immutable workspace identity and a
verified org identity. Tier 2 separates approval ownership but retains local replay and
execution-evidence limitations described in the linked guides.

`solution-lead` is an optional workspace profile for a consultant who leads
delivery across several clients; the product works with any firm or independent consultant.

A firm with an AI-use policy can set a workspace to build-only: a Claude Code or
Antigravity hook then keeps the assistant away from client orgs and client context, best-effort and not a
sandbox. See [build-only mode](docs/ai-access.md) for what it does and does not cover.

Run `torque --help`, `torque workflows list`, or a route's `--help`. Stateful
operations accept `--workspace PATH --client NAME` and retain their explicit org
arguments. The legacy `torque revert ...` grammar remains available for existing
scripts. Recovery is scoped; it is not a universal undo or Salesforce backup.

Metadata recovery verifies captured post-deployment state while holding an org
lease shared across aliases and workspaces for the same OS account. Unknown state
blocks recovery, including with `--force`. Data upsert sends an atomic external-ID
request; incomplete capture returns nonzero even when the write succeeded. Lesson
updates preserve unreadable state and serialize concurrent changes. See
[package safety and limits](docs/package-migration.md#recovery-and-local-state-safety).

`torque doctor --for salesforce` checks local dependencies without contacting an
org. After installing a newer version, `torque workspace upgrade PATH --check`
previews workflow updates; applying them preserves local customizations.
[Workspace upgrades](docs/workspace-upgrades.md).

## Product direction

Torque continues the author's earlier consulting toolkit, used in daily work for
about six months. Legacy command mappings and package provenance are documented
below; that history does not qualify every current capability for live use.

Torque should earn its place by reducing repeated investigation, missed
acceptance checks and handoff effort. Platform skills, AI assistance, deployment
pipelines and business traceability already exist elsewhere. We compare against
them and integrate where appropriate; command count is not a competitive claim.

- [Current tools and product comparison](docs/competitive-landscape.md)
- [How useful findings are incorporated](docs/research-adoption.md)
- [Product direction and release work](docs/product-direction.md)
- [Comparison protocol](docs/benchmark-protocol.md)
- [Validation and limitations](docs/validation.md), [next live acceptance scenario](docs/live-acceptance.md)
- [Client adoption and provider data boundaries](docs/client-adoption.md), [optional engagement worksheet](examples/client-data-boundary.md)
- [Contributing](CONTRIBUTING.md), [security and data boundaries](SECURITY.md), [changelog](CHANGELOG.md)
- [Continuity with the earlier toolkit](docs/continuation.md), [workflow mapping](docs/workflow-continuity.md), [package provenance](docs/package-migration.md)

Client data, credentials, org mappings and employer documents belong in private
workspaces, outside the public source. Git ignore rules do not untrack files
already committed. The assistant and any external tools retain their own data
handling and account requirements. Torque itself has no hosted backend or
embedded telemetry service.
