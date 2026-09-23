# Conversational workflow continuity

Torque carries forward the working consulting framework: client onboarding,
discovery, diagnosis, implementation, migration, QA, recovery, meeting processing,
documentation, session continuity and lessons. It is not limited to advisory
reports. A request to investigate and fix an issue should continue through the
authorized change and verification using the tools available in the user's environment.

All **42 source command names** are retained. Eleven additional names make discovery,
portable context, session resumption and four delivery recipes explicit. The complete machine-readable
index is `workflows/catalogue.json`; `torque workflows list` displays it and
`torque workflows show <name>` displays a recipe.

## One workflow, multiple ways to start it

Natural language is sufficient: “Load this client and continue the Flow fix,”
“Prepare and run this migration,” or “Verify it as the support user and save a
handoff.” The `.claude/commands/` files provide familiar slash names for compatible
hosts. The `workflows/` recipes are agent-neutral and contain the same instructions.

The catalogue distinguishes:

- **guided:** a recipe that the assistant performs through normal CLI, MCP, browser
  or document tools. `/audit-org` does not imply a native `torque audit-org` exists.
- **native:** a recipe with a real executable runtime or package delegate. The
  assistant still chooses correct inputs and interprets the actual result.
- **compatibility:** a retained name whose historical behavior has changed.
  `update-jsc` leads to Torque update guidance; `qa-token-*` manages explicitly
  requested legacy skip records and is never needed for ordinary work.

`implementation` identifies the native route or guided recipe. `source_command`
is the original name for the 42 retained commands and null for a new addition.
Native route names are not full shell commands to execute without arguments.

## Portable runtime and private context

The core context interfaces are:

```sh
torque workspace init <private-path> --name <workspace-name> --profile generic
torque client add <client-name> --workspace <private-path> --org <org-alias>
torque context --workspace <private-path> --client <client-name> --json
torque session add --workspace <private-path> --client <client-name> --summary <text> --status prepared
torque session list --workspace <private-path> --client <client-name> --json
torque session show <entry-id> --workspace <private-path> --client <client-name> --json
torque handoff --workspace <private-path> --client <client-name> --output <handoff-path>
```

Angle-bracket values are placeholders to replace with actual arguments. Use safe
argument passing rather than evaluating user text as shell code. Workspace/client
context is explicit and private, outside the public source checkout. A workspace
profile configures conventions; it does not itself grant org access or employer approval.

Delegated routes include `advisory`, `qa`, `revert`, `logs`, `browser`, `meeting`,
`lesson`, `probes` and `ai-regression`. Inspect each route's `--help` for its installed package
contract and optional dependencies. The wrapper consumes `--workspace` and `--client`
for scoping and otherwise passes downstream arguments through. Thus the recovery
listing is intentionally `torque revert revert show --org <alias>`, while snapshot-aware
deployment, when selected, is `torque revert deploy ...`.

Normal Salesforce tools remain available. A missing optional package surface does
not block unrelated work. Some recipes are broader than a native helper: arbitrary
browser QA, business discovery and full engagement handoffs still require assistant
judgment and the environment's actual tools.

The `probes` delegate generates Apex test scaffolds; normal Salesforce tools compile
and execute them after review. The `ai-regression` delegate replays supplied AI
fixtures and checks output through its inherited provider adapter; it is not a
Salesforce production-deployment check. Meeting extraction runs locally; its optional
native `--analyze` adapter currently uses Gemini CLI for selected frame images.
The conversational recipes remain neutral about which model the user normally runs.

## Retained command map

| Group | Original command names | Continued purpose |
|---|---|---|
| Workspace | help, status, connect-org, new-client, update-jsc | Discover capabilities, connect environments, establish client context, update the selected installation. |
| Investigation | advisory, audit-org, compare-orgs, diagnose, field-audit, flow-analyzer, security-review | Understand current configuration, dependencies, failures and actual access, then remediate when requested. |
| Delivery | preflight-org, retrieve-current, prep-changeset, prep-migration, validate-change, verify-change, sandbox-refresh | Prepare and execute change with relevant validation, reconciliation and current-state verification. |
| QA | qa, qa-browser, qa-multiprofile, qa-sanitize-replay | Select tests, use actual browser identity, inspect side effects, and scan replay credentials without leaking them. |
| QA compatibility | qa-token-grant, qa-token-show, qa-token-revoke | Optional inherited skip-record management; no successor action depends on these tokens. |
| Recovery | snapshot, undo, revert, revert-show, revert-preview, revert-discard | Capture useful exact pre-state, inspect drift, execute supported inverse operations, and record honest recovery limits. |
| Learning | lesson, lesson-show, lesson-helpful, lesson-stale, collect-lessons | Capture, inspect and curate useful scoped knowledge without forced queues or cross-client merging. |
| Continuity and documents | export-session, handoff, process-meeting, release-notes, training | Turn work and meetings into durable records, clear requirements, maintainable handoffs and audience-appropriate documents. |

Added names: **context, discovery, session-save, session-resume, logs, probes,
update-torque, triage-alert, gift-payments, grants-outbound-funds,
requirements-to-build**. The catalogue is the complete list used by runtime discovery and
packaging, rather than a second manually maintained executable registry.

## Preserved behavior and deliberate corrections

The port preserves breadth while correcting brittle defaults:

- Client schemas, settings, namespaces, package versions and baseline orgs come
  from the selected context and live evidence, not a former employer's objects.
- Existing authorization carries through execution. There is no universal shell
  interception, source-edit shield, writable-org list, real-terminal token or
  maintainer workflow in the conversational foundation.
- Current metadata, relevant validation, exact deploy/job correlation and useful
  recovery evidence remain practical delivery habits. They are not a second policy
  system or an excuse to stop at a prepared checklist.
- A missing grant in one permission set does not prove the intended user's full
  access. An active Flow version is checked independently from retrieved source.
  Job completion, row success and business behavior are separate findings.
- A low fill rate or local search miss does not justify deleting a field. A
  deployment error does not automatically justify disabling validation rules.
- Snapshots distinguish exact private recovery payloads from redacted summaries.
  An undo targets actual task changes and preserves unrelated edits; it does not
  assume the previous git commit represents the session.
- Browser tests can write records. Admin coverage, user-session coverage, backend
  diagnostics and human assertions are labelled according to what actually ran.
- Session summaries are appended after meaningful work; lessons are useful scoped
  records, not a required automatic capture/review lifecycle.
- Provider choice follows installed tools and the user's environment. Meeting
  extraction is separate from optional model analysis and its outbound inputs.
- Update instructions identify the actual installation and configured upstream;
  they do not assume a particular GitHub account or a historical two-clone model.

Five concise rules under `.claude/rules/` cover context, delivery, evidence/QA,
continuity, and artifact handling. They replace the source's many employer- and
incident-specific rules. Generic implementation details live in the relevant recipe
and package help rather than being injected into every task.

## Keeping adapters synchronized

`workflows/*.md` and `workflows/catalogue.json` are the authoring sources. Claude
command adapters embed the complete recipe so installed commands work outside a
source checkout. Regenerate or verify them in the source checkout:

```sh
python3 workflows/sync_adapters.py
python3 workflows/sync_adapters.py --check
```

The sync tool updates only command files named in the catalogue; it does not delete
user-added adapters. Root packaging bundles the catalogue and recipes for the
agent-neutral runtime. Validation should check all 42 source-name mappings, every
recipe/adapter pair and the actual installed native parser contracts.

## Scope of this port

The conversational continuity layer is implemented locally. Its source lineage is
the 42 command files reviewed in the JusticeServer working workspace, generalized
without importing client records, internal contacts, org aliases, managed-package
source, token-bearing URLs or employer branding. Historical source names remain
only where necessary for command compatibility and provenance.

Recipe completeness is not a claim that every live tool has been tested. Offline
catalogue/synchronization checks establish local packaging and instruction
continuity. Live Salesforce, browser identity, provider integration, recovery and
business outcomes need their own scoped execution evidence.
