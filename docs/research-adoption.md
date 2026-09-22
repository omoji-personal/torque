# Applying useful findings

Updated 2026-09-08 through alpha 8. Research informs the implementation and everyday
recipes. This is a maintainer reference for material decisions, not a form the
consultant must complete or a new approval process.

The [current-product review](competitive-landscape.md) contains the primary-source
comparison; [product direction](product-direction.md) sets priorities. The table
below distinguishes code already implemented, guidance actually delivered and
work still needing evidence. A recommendation is not a completed integration.

| Useful finding | Incorporated into Torque | Current evidence or remaining proof |
| --- | --- | --- |
| Platform skills and AI-assisted delivery are established capabilities | Shared tools rule, onboarding, changeset and validation recipes prefer current installed official Salesforce tools and existing project conventions | Packaged guidance; hands-on coexistence with the current official plugin and external delivery stacks remains to test |
| Existing CCI/Hardis projects already encode delivery knowledge | Onboarding retains real project paths, commands and artifact conventions in optional client context; deployment planning reuses the chosen pipeline | `context/tooling.md` is read through the existing client-scoped context loader; no new pipeline or schema |
| Mature migration tools handle relationships and mappings | Migration recipe considers configured SFDMU, CCI datasets and Hardis workspaces before custom row scripting; retains mapping/config scope | Guided composition; no new automatic migration adapter or unsupported CLI flags |
| Simulation cannot establish target-write behavior | Migration instructions separate simulation, sandbox rehearsal, actual row outcomes and reconciliation; technical and business observations remain distinct | Existing change-record and exact-job evidence distinctions are implemented; external tool reports remain reported unless a specific verifier evaluates them |
| Requirements/rationale systems already exist in client teams | Discovery links the actual source of requirements to optional outcome/criteria/decision records, preserving source status | Change records, criteria/history, scoped context and handoff are implemented; no parallel requirement catalogue required |
| QA must establish requested behavior, not just a green tool exit | QA and verification recipes start with intended actors and business cases, reuse adequate existing tests, and add only missing coverage | Empty/deferred/browser-identity/false-PASS regressions corrected; full non-admin Flow journey remains to qualify live |
| A newcomer needs an immediately useful example | A no-org synthetic discovery-to-handoff demo with real local checks and clearly unrun live criteria | Clean installed demo and fresh-process continuation pass; outside-user usability trials remain open |
| Durable workflows need updates that preserve local work | Native template updater plus update recipe preview/apply instructions | Concurrent/interrupted update and local-customization regressions pass; no automatic overwrite of client edits |
| Repeatable public examples should avoid client data | Synthetic demo and migration recipe use small authored examples; consider Snowfakery for complex related-data scenarios when it adds value | No extra generator dependency installed; existing small demo remains self-contained |
| Audits are useful when defects are reproduced and corrected | Reproduced recovery, authoritative org identity, bulk-result, CSV and browser-diagnostic defects corrected; original failures retained | See validation for exact artifacts, offline regressions and bounded live outcomes; named-user browser switching remains unqualified |
| Commercial no-training terms do not establish zero retention or client authorization | Client-adoption guide, explicit data paths in SECURITY.md and optional engagement worksheet | Current primary policy research; no particular provider account, client agreement or ZDR configuration has been verified |
| A successful summary can contradict supplied failure details | Deployment classification now rejects supplied component/test failures and inconsistent counts while preserving exact job identity | 46 new regressions and retained-report compatibility; a pure parser repair with no submission or retry behavior |
| Flow cleanup can require an exact version-qualified selector | Live-acceptance guide incorporates platform guidance, fresh version/interview checks and exact-job continuation | Fixture cleanup practice; no new automatic Flow deletion or rollback feature |
| Generated tests can pass without exercising the requested behavior | Typed, declaration-scoped Apex drafts call supported targets and keep unresolved expectations visibly failing; QA distinguishes generation from compilation/execution | Reproduced false-success cases corrected; the bounded draft/adapted compiler and test sequence completed with independent contract checks and exact owned-class cleanup |
| Preserving a customized file is insufficient if lookup reads a different copy | Selected-workspace workflow lookup now reads the actual private recipe before using a packaged fallback | Source and installed workflow checks require the customized content itself |
| Resumption and handoff should carry the same scoped working context | A shared bounded note reader feeds both, retaining the selected-client boundary and explicit Unicode character limit | Notes, journal and change/evidence remain available together; no automatic external sharing |
| Interrupted onboarding should not strand a client name | Complete client directories are privately staged and atomically published, with a short internal lock for cooperating creators | Failure, process termination, retry and concurrent-creation regressions; no user-facing recovery ritual |
| Competitive claims need measured incremental benefit | Same-assistant/tool baseline, counterbalanced task pairs, timing and handoff criteria | Benchmark protocol is prepared; no comparative result or superiority claim exists yet |
| CLI report caches can override an explicit org argument | MetaAPI receipts fetch the exact deployment directly from the selected org through the documented REST endpoint | Synthetic cached-org counterexample corrected; live existing success/failure records verified without new deployments. [Salesforce status endpoint](https://developer.salesforce.com/docs/atlas.en-us.api_meta.meta/api_meta/meta_rest_deploy_checkstatus.htm) |
| Salesforce can return a longer representation of the same job ID | Both requested and returned deployment IDs are validated and compared using their case-sensitive identity; raw IDs remain in receipts | Live 15-to-18 response observed; valid forms pass while malformed checksums and different jobs fail |
| A green source test can exercise an older installed copy | The offline runner pins source package paths for pytest and fresh subprocesses; complete runs retain the executable suites even when pytest options take operands | Synthetic stale-install reproduction and source-import regression; wheel behavior remains a separate installation check |
| A saved reference can drift or disappear before the next session | Session resumption and handoffs recheck hashes; malformed records identify their file; doctor inspects the selected client's full record history | Local missing, changed, unreadable and cross-client cases; no result is promoted to independent business verification |
| An externally installed CLI can miss the source/private boundary | Workspace and demo creation inspect the destination's ancestors as well as the running installation | Synthetic Git checkout, worktree and extracted-source tests; unrelated consulting repositories remain usable |

## Incorporation practice

For a material useful finding, choose the smallest effective response: fix the
implementation, improve a packaged recipe/example, reuse an existing tool, or
retain a concrete next verification task. Record a source and the reason when they
help future maintenance. Avoid adding a dependency, wrapper or workflow step solely
to match another product's feature list.

Update the relevant source recipe, synchronize its adapters and bundled resources,
and verify the installed distribution contains the change. Existing workspaces can
use `torque workspace upgrade`; local edits are preserved. Findings do not become
runtime restrictions, global tool interception, compulsory checklists or repeated
permission prompts.

The current update adds **guided composition** with established tools. It does not
claim vendor certification, tested native CCI/Hardis/SFDMU adapters, automatic parsing
of every external report or live compatibility with products we have only researched.
[The comparison protocol](benchmark-protocol.md) defines how to establish usefulness.
