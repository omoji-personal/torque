# Product direction and release work

Decision date: 2026-09-07. Primary users are Salesforce consultants and small
consulting teams who work across clients. BackOffice Thinking is one optional
private configuration. The practical JusticeserverClaude workflow remains the
foundation; a hosted service, replacement IDE or new deployment engine is not
required to deliver the next useful product.

## What Torque should be excellent at

A consultant should be able to resume a client's change after an interruption,
understand the business request and decisions, see what actually passed or failed,
continue with existing tools, and leave a handoff another consultant can use.
The assistant should maintain useful continuity during work without requiring a
form, new approval ceremony or prescribed sequence for every operation.

The [market review](competitive-landscape.md) covers Salesforce CLI, official
skills/plugin/MCP, Agentforce DX, CumulusCI, sfdx-hardis, SFDMU, Snowfakery, Gearset,
Copado, Elements.cloud and Salto. AI assistance, local execution, full lifecycle
workflows and requirement traceability all have precedents. Torque's proposed
advantage is a compact, portable assembled experience across consulting clients
and assistant hosts. That advantage remains a hypothesis until measured.

| Decision | Why | Evidence needed |
| --- | --- | --- |
| Keep private client workspaces and optional change records | Preserve engagement meaning across sessions without a Torque account | Correct resumption, explicit target and no client mixing |
| Keep current Salesforce CLI/skills/MCP as execution and platform expertise | Official tooling already covers this domain deeply | Coexistence with the current official setup; no conflicting hooks/defaults |
| Compose with CCI, Hardis, SFDMU and commercial pipelines | Teams already have reliable delivery and migration investments | At least one real existing-project integration, preserving native diagnostics |
| Retain exact technical observations beside business acceptance | A successful deployment does not establish intended-user behavior | Positive, negative, repeated-edit and non-admin cases; no empty green runs |
| Lead onboarding with a no-org demo | A newcomer should see a useful outcome before configuring accounts | Outside-user completion time, setup failures and independent handoff quality |
| Keep optional unqualified adapters visibly experimental | Broad libraries are not the same as verified support | Adapter-specific live evidence before broader claims |

## Implementation through alpha 6

The public product work adds a synthetic offline demo, optional engagement records,
captured evidence, exact deployment observations, client context/handoff integration,
workspace upgrades and simpler delivery routes. The audit also corrects false
success in QA/browser paths, browser identity and credential handling, bulk capture
coverage, and operation lease maintenance. These fixes improve actual behavior;
they are not a claim that every inherited library is fully qualified.

The rigorous live exercise then exposed defects in org classification, exact
metadata restoration, Bulk API result handling and CSV line endings. Alpha 5
incorporates those repairs, retains original failed attempts and distinguishes
partial application from success. The [validation record](validation.md) separates
tested versions, API business outcomes and still-blocked non-admin browser cells.

Alpha 6 adds a pure result-parser repair: a successful summary cannot override
supplied component/test failures or contradictory test counts. Retained real
reports and a fresh installed artifact qualify this change separately from the
earlier live mutations. Client-adoption guidance also makes local and provider
retention boundaries explicit without adding runtime permission steps.

## Release sequence

| Stage | Work and acceptance | Current boundary |
| --- | --- | --- |
| Local development artifact | Core regressions, independent code review, docs/examples, complete offline harness, wheel/sdist inspection, fresh installed CLI | See validation.md for the exact build tested |
| Public alpha publication | Review final diff and package contents; run remote CI on the release commit; verify project/index ownership, version, license notices and artifact hashes; publish explicitly | No push, release, package publication or public support promise has occurred |
| Supported consulting journey | Two clean synthetic runs with business-positive, negative, repeated-edit, non-admin, recovery and cleanup evidence; fresh assistant resumes midway | Flow API business cases, scoped recovery and bulk outcomes have bounded evidence; non-admin browser cells and a second complete journey remain open |
| Adoption proof | Three outside users complete onboarding; five practitioners attempt a handoff exercise; test two assistant hosts and an existing delivery stack | Protocol prepared; no outside-user results invented |
| Comparative claim | Run counterbalanced baseline trials with the same assistant/model and official tools; publish nonprivate raw measurements and failures | No superiority or time-saving percentage is established |

Publication is a maintainer action. These stages do not add runtime permission
checks or prevent a practitioner from using the prepared local product within
its documented scope. An alpha can be useful before all optional adapters or
leadership claims are qualified, provided its supported scope is stated plainly.

## Practical team adoption

Demonstrate one synthetic engagement, then propose a small voluntary pilot using
an agreed sandbox and the team's existing assistant/data policies. Show where
context is stored, what external tools receive, how a colleague resumes work,
and how to stop using Torque without losing plain files. Choose a real task and
compare delivery effort and handoff quality with the current process. Capture
failures and actual setup cost. Do not promise a productivity percentage, employer
approval, compliance certification or rollout before those results exist.

[Client adoption guidance](client-adoption.md) and an optional data-boundary
worksheet explain managed accounts, provider retention, local artifacts and client
authorization. They help document the actual arrangement without adding runtime
approval gates or claiming that a business subscription guarantees zero retention.

The public pitch should show an actual change, interrupted resumption and useful
handoff, with optional technical evidence. A short recording can explain it, but
a runnable example and independent continuation are stronger proof than feature
counts or a polished landing page alone.

## Applying the research

The [adoption map](research-adoption.md) connects useful findings to implemented
code, packaged workflow guidance and remaining verification. Alpha 4 carries the
existing-stack, migration-simulation and business-first QA findings into daily
recipes; it adds no mandatory client process or new runtime dependency.
