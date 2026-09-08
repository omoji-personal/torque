# Torque public landscape and product direction

Checked **2026-09-07** against current official documentation and project-owned repositories, plus the Torque 2.0 alpha 2 audit baseline. This is a capability and positioning review, not a hands-on benchmark of competing products. Recommendations and numerical targets below are proposed criteria, not measured results. The market research involved no competitor account access or hands-on product trials.

Torque can pursue an excellent public product by making the daily Salesforce consulting engagement unusually easy to continue, verify, and hand over. Its broad JSC foundation is valuable implementation experience. It does not establish superiority over the tools below. The strongest next investment is a polished, reproducible client-task journey that works with established execution tools and measurably reduces repeated investigation and handoff effort.

## Current competitive capability matrix

“Implication” is product analysis. It does not assert that an undocumented competitor feature is absent.

| Tool / official source | Documented capabilities relevant to Torque | Overlap and implication for Torque |
|---|---|---|
| [Salesforce CLI](https://developer.salesforce.com/docs/platform/salesforce-cli-reference/guide/cli_reference.html) | Official executable interface and command reference for Salesforce operations. Its ecosystem supplies org, metadata, data, testing, packaging, and agent commands. | Keep it as an execution foundation. Another spelling of retrieve/deploy/test adds little value. Torque should preserve native diagnostics and exact job identities while relating them to the client task. |
| [Salesforce Skills Library](https://github.com/forcedotcom/sf-skills) and [official Salesforce development plugin](https://github.com/forcedotcom/sf-skills/tree/main/plugins/builder/salesforce-development) | Portable skills cover Flow, Apex, SOQL, objects, permissions, LWC, and Agentforce. The Claude plugin auto-detects DX context, provides architecture review, metadata/API guidance and language-server tools. It already tracks Connect → Project → Build → Test → Deploy → Observe using successful actions. | Natural language, skills, org context, review agents, and action-based progress are existing capabilities. Compose with official platform expertise; concentrate Torque's own work on engagement context, decisions, intended-user acceptance and transferable handoff. |
| [Salesforce DX MCP](https://github.com/salesforcecli/mcp) | Toolsets for orgs, metadata, data, users, testing, DevOps Center and other domains; per-tool GA labels; configuration for multiple clients; selective/dynamic tool discovery. | A Torque MCP server would be an interface choice, not a differentiator. Start with existing CLI/MCP routes. Expose Torque's own context and work records only if host integration proves useful. |
| [Agentforce DX](https://developer.salesforce.com/docs/ai/agentforce/guide/agent-dx.html) | Author, validate, preview, publish, retrieve and test Salesforce agents across CLI, VS Code and Salesforce UIs; supports simulated and live preview and source-controlled metadata. | Torque's optional prompt regression harness is not an equivalent Agentforce development or evaluation platform. Retain its narrower output-contract role; use official agent tools for genuine Agentforce work. |
| [CumulusCI concepts](https://cumulusci.readthedocs.io/en/latest/concepts.html), [data automation](https://cumulusci.readthedocs.io/en/latest/data.html), [Robot acceptance testing](https://cumulusci.readthedocs.io/en/latest/robot.html), [GitHub Actions](https://cumulusci.readthedocs.io/en/latest/github-actions.html) | Reusable tasks and ordered flows for org builds, dependency installation, metadata, datasets and tests; Salesforce browser acceptance tests and CI integration. | Deterministic multi-step Salesforce automation, reproducible QA orgs, data fixtures and browser testing are established territory. Use or interoperate with CumulusCI for repeatable demo setup and existing client projects instead of rebuilding a general flow runner. |
| [sfdx-hardis](https://sfdx-hardis.cloudity.com/), [agent workflows](https://sfdx-hardis.cloudity.com/salesforce-ci-cd-agent-skills/), [data workspaces](https://sfdx-hardis.cloudity.com/salesforce-ci-cd-agent-data-workspaces/) | CLI and VS Code tools; Git-based delivery, deployment actions, metadata backup, monitoring, documentation and AI assistance. Agent mode covers story creation/save and many noninteractive operations. SFDMU workspaces connect data configuration to deployment actions. | The broad daily-work toolbox is already competitive. Do not market command count, AI documentation, data workspaces or “all-in-one” as distinct advantages. Integrate its outputs into engagement work when a client already uses it. |
| [SFDMU repository](https://github.com/forcedotcom/SFDX-Data-Move-Utility), [simulation limits](https://forcedotcom.github.io/SFDX-Data-Move-Utility/faq/data-migration-with-plugin/why-are-there-differences-in-failures-between-simulation-and-non-simulation-modes/) | Org/CSV data migration across related objects; CRUD, circular references, mapping, composite keys and anonymization; desktop GUI. Simulation checks configuration/source data without target writes and cannot reproduce actual target-write failures. | Use a mature migration engine where appropriate. Torque adds source requirements, mapping decisions, rehearsal observations, reconciliation and a bounded recovery record. A simulation or successful process exit must not become an end-to-end acceptance claim. |
| [Snowfakery](https://github.com/SFDO-Tooling/Snowfakery) | YAML recipes generate synthetic data with relationships; output to files/databases and Salesforce when embedded in CumulusCI. | A useful public-example dependency. Build reproducible business scenarios with synthetic data; no need for Torque to invent another general fake-data generator. |

Version and status discipline matters. Salesforce's August 2026 [headless development announcement](https://developer.salesforce.com/blogs/2026/08/headless-development-with-skills-and-a-claude-code-plugin) describes its official plugin, while the repository now documents further capabilities. The Skills Library warns of rapid changes. Product-specific docs say [Code Analyzer MCP support ended in June 2026](https://developer.salesforce.com/docs/platform/salesforce-code-analyzer/guide/mcp.html) and [LWC MCP tools were superseded by skills in August 2026](https://developer.salesforce.com/docs/platform/lwc/guide/mcp-intro.html); this does **not** mean the whole DX MCP server ended. Favor the current [Code Analyzer skills](https://developer.salesforce.com/docs/platform/salesforce-code-analyzer/guide/skills.html), pin tested dependencies, and document compatibility rather than copying stale tool inventories.

Local execution is also not exclusive: sfdx-hardis documents a [no-backend/no-embedded-telemetry architecture](https://github.com/hardisgroupcom/sfdx-hardis/security). Avoid treating private folders, open source, model choice, or local tools as sufficient competitive claims.

## What alpha 2 has, and the gaps that matter

These are the alpha 2 audit baseline, before the alpha 3 improvements described in the changelog; they are not an audit of every package. The [README](../README.md), [validation record](../docs/validation.md) and [continuation contract](../docs/continuation.md) identify the current scope honestly: 49 recipes preserve all 42 JSC names; native context, journal and handoff coexist with guided work and inherited libraries. Offline tests and an installed package smoke test are documented. A bounded live metadata/data/recovery exercise is documented, while representative Flow behavior and non-admin browser acceptance remain open.

| Gap | Current evidence | Highest-value improvement |
|---|---|---|
| Public first-use experience | README begins with Python environment/install and several workspace commands, then open-ended prompts. | Give newcomers one clear example outcome and a no-org sample path. Show a useful handoff immediately, then guide connection to their own sandbox. State prerequisites and costs of optional host/provider tools plainly. |
| Work structure is mostly prose | [Session records](../src/torque/workspace.py) contain a summary, user-reported status and optional file/hash reference. | Introduce a small optional work record linking objective, acceptance criteria, decisions, artifacts, observations and next steps. Let the assistant maintain it during work. It must save effort without requiring the user to fill out a form or follow a fixed sequence. |
| CLI handoff is a journal export | [Renderer](../src/torque/workspace.py) renders chronological summaries and evidence references; the [guided recipe](../workflows/handoff.md) asks the agent to enrich it. | Render a concise current-state handoff with business result, changed components, open questions, verified/unverified assertions, recovery limits and next owner action. Keep the underlying history accessible. Test it with a different consultant. |
| Evidence is not yet joined into one acceptance story | Native modules produce useful evidence, but journal hashes do not evaluate content. Actual scope varies by module. | Add small importers for exact metadata jobs and selected tests, retaining source files and interpretation. Link each observation to a particular criterion, target, actor and source revision where known. Do not auto-promote a whole task because one test passed. |
| Cross-session scale is unproven | [Context loader](../src/torque/workspace.py) reads client Markdown and recent session entries. | First test many ordinary engagements. Then add task selection, concise current-state summaries and explicit stale-source cues when they reduce repeated reading; a vector database is not a prerequisite. |
| Broad QA needs business proof | Validation explicitly leaves browser identity, non-admin behavior, session restoration, cleanup, meaningful Flow outcomes and some optional adapters untested live. | Finish one representative Flow journey before expanding the menu. Publish supported capabilities and observed limits separately; optional untested modules can remain labeled experimental. |
| Tool composition and outside adoption are unproven | Current docs do not demonstrate using Torque alongside the newest official plugin or an external consultant's established CCI/Hardis project. | Test coexistence with one official Salesforce host setup and one existing delivery stack. Measure incremental benefit against the same assistant/tools without Torque. |

The larger gap is product validation, not lack of libraries. Alpha 2's source continuity and narrow live recovery proof are a substantial starting point, but they do not yet establish that a stranger can use the complete experience reliably.

## Defensible differentiation to develop

Proposed positioning: **“Torque keeps a Salesforce consulting engagement connected—from the client's request to a verified change and a handoff someone else can continue.”** This is an intended product promise that must be tested. It is not a claim that no other product can do these things.

Three connected areas are worth owning:

1. **Engagement continuity:** retain client vocabulary, scope, decisions and unresolved questions across sessions, repositories and assistants, with fast resumption and deliberate client selection. This extends beyond a default org or branch status.
2. **Business acceptance that survives handoff:** connect the requested behavior to source changes and actual observations by the intended user, including negative cases, partial results and recovery limits. Combine this into a short client explanation and a technical continuation record.
3. **Practitioner-maintained reusable journeys:** turn lessons from daily delivery into tested, approachable examples that use current platform tools. The strength is the observed quality of the assembled experience and its maintenance, not the number of prompts or wrappers.

These are contestable advantages, not a moat. Competitors can expand into them. Durable credibility comes from independent use, reproducible examples, precise documentation and a track record of fixes. Preserve the broad daily-use framework while making the common path simpler. BackOffice Thinking can be one private application of the generic framework; it should not define public schemas or become a prerequisite for publishing.

## Highest-value public user journey

**A consultant receives a small automation request, completes it in a sandbox, changes assistants halfway through, and hands it to another consultant without retelling the engagement.** Use a wholly synthetic service-request scenario that applies across industries.

1. **See the result first.** The public page shows a short client-facing outcome and expandable technical evidence. A no-auth sample workspace lets users inspect requirements, a deliberately incomplete session, and a completed example. Supplied sample evidence is visibly illustrative.
2. **Start from a client request.** “When a service request becomes urgent, create one follow-up task for its owner; repeated edits must not create duplicates. Ordinary users must be able to use this process.” The assistant turns this into explicit positive, repeated-edit, ownership and access cases.
3. **Ground and build.** Select a disposable org, retrieve current metadata, investigate relevant automation/access, and implement the change through existing Salesforce tools. Record the important choices and actual source diff. Test source/metadata validity and the intended behavior separately.
4. **Interrupt and resume.** Stop after validation or after an observed failure. A fresh session, preferably in a second supported assistant, reconstructs what happened, detects remaining verification, and continues without repeating completed work or silently switching targets.
5. **Verify as the intended user.** Exercise urgency, repeated edits, ordinary-user access and a negative case. Record exact actor/context, result and evidence; retain a failed observation if corrected later. API admin checks alone are insufficient for this claim.
6. **Hand over and recover.** Generate a concise client explanation, acceptance table, technical handoff and supported recovery instructions. A second person identifies what changed and performs one small follow-up without author coaching. Demonstrate a limited recovery and cleanup with observed results.

This journey shows discovery, platform work, QA, recovery and continuity in one story. A short recording is useful, but the runnable scenario and another person's successful continuation are stronger evidence. Use existing CCI/Snowfakery setup if helpful; add a GUI only where testing finds the current host/CLI experience prevents users from completing this journey.

## Measurable publication and leadership criteria

These are suggested release-quality targets, not new runtime approvals or restrictions. Publish a clearly labeled development alpha when its supported scope is reproducible; keep broader routine-use claims contingent on relevant evidence. No employer date is assumed.

| Claim / milestone | Proposed measurable evidence |
|---|---|
| Public alpha can be installed and explored | A clean tagged artifact installs outside the checkout on every advertised OS/Python combination. All documented quick-start commands run. Three outside users reach the no-auth sample handoff without author intervention; target median under 10 minutes and record actual prerequisites/setup time. |
| A complete consulting journey is supported | The synthetic journey passes twice in clean org state, including positive, negative, repeated-edit and non-admin cases. All expected tests actually execute; cleanup and scoped recovery are observed. Publish exact tool versions and limitations. |
| Continuity works across clients and hosts | Run a prepared matrix with two clients, two tasks each, and two supported assistant hosts. A fresh session identifies the selected task, remaining acceptance checks and correct target every time; no other client's sentinel content appears. This tests the product contract, not an OS security guarantee. |
| Handoffs are useful to someone else | At least five Salesforce practitioners unfamiliar with the implementation perform a resume/follow-up exercise; target four of five without author coaching. Score objective, current state, unresolved checks, changed artifacts and next action against a fixed answer key. Report failures. |
| Torque reduces work | Compare at least 10 paired, equivalent task/resume trials using the same model/tools and comparable tasks with and without Torque; counterbalance order and include the official plugin baseline. Track active minutes, repeated context questions, corrective edits and acceptance omissions. A suggested success target is ≥20% lower median resumption/handoff effort with no increase in missed criteria; do not advertise this before measurement or generalize beyond the sample. |
| Public distribution is maintainable | Remote CI verifies the advertised package matrix; artifact inspection excludes private fixtures and secrets; links/examples stay executable; licenses/provenance and a practical issue template exist. Changes in official skills/plugins receive compatibility checks. Keep these as maintainer work, not daily user ceremonies. |
| “Industry-leading” becomes defensible | Define the precise category being claimed, publish the comparison protocol and raw nonprivate results, let outside users reproduce them, and demonstrate sustained use across more than one firm/team. There is no honest single test count or feature count that establishes this label. |

Recommended order: polish the sample/resume/handoff experience; join task criteria to existing evidence; verify the representative user journey and compatibility; collect independent comparison results; then expand whatever users actually need. Avoid a SaaS rewrite, another deployment engine, or a large catalog expansion before these observations show where they would help.

## Commercial products and adjacent competition

These are documented vendor capabilities, not hands-on performance results. Public
pages can describe announced features or different paid tiers; availability and
quality need account-level verification before a comparative trial.

| Product | Current primary evidence | Implication |
|---|---|---|
| Gearset | [Delivery platform](https://gearset.com/solutions/) combines org intelligence, AI-assisted changes, deployment pipelines, testing, backup and monitoring. [Cam launched September 2, 2026](https://gearset.com/newsroom/press-releases/). [UI testing documentation](https://docs.gearset.com/en/articles/12960167-introduction-to-automated-ui-testing) describes repeatable Salesforce user journeys. | Org-aware AI, end-to-end delivery and UI testing are not distinct Torque claims. Integrate with a team's existing release tooling and measure the additional consulting continuity benefit. |
| Copado Agentia | [Agentia Advanced](https://www.copado.com/go/agentia-advanced) documents impact, metadata, testing and request-to-production traceability. [Current documentation](https://docs.copado.com/home/en-us/) includes an AI Context Hub and robotic testing. | Context-grounded agents and lifecycle orchestration already have commercial competition. Torque's portable files and flexible use across consulting clients are proposed usability advantages to test, not proof that Copado lacks context. |
| Elements.cloud | [Why it exists](https://support.elements.cloud/en/articles/15459980-why-it-exists-understanding-the-business-rationale-behind-your-metadata), dated August 6, 2026, connects components to linked tickets, requirements and rationale. [Process-led agent design](https://support.elements.cloud/en/articles/10470159-designing-agents-quick-start-guide) covers scope, stories, instructions and testing. | Business rationale linked to metadata is also existing territory. Torque should offer a compact portable working record, not claim to have invented requirement traceability or compete with full process-mapping breadth. |
| Salto | [Admin AI overview](https://www.salto.io/blog-posts/ai-for-salesforce-admins-not-just-your-reps) addresses understanding and documenting Salesforce configuration and dependencies. | Investigating an unfamiliar org and generating explanations are competitive baseline capabilities; do not assert absent features from a short public article. Verify the product directly before scoring richer workflows. |

The differentiator to evaluate is the assembled experience: a consultant carries
business requirements, choices, actual observations and next actions across clients,
sessions and assistant hosts, with portable local artifacts and no Torque service
account. Each attribute has precedent. The combination is worth building only if
independent practitioners can resume and hand over work with less effort and fewer
acceptance omissions than an equally configured baseline.

## Comparison discipline

- Distinguish documented, demonstrated, tested, unavailable and unknown. A missing
  public mention is not an absent feature.
- Use current Salesforce official tools plus the same assistant/model as the first
  baseline. Compare commercial products only with access to the relevant features;
  do not infer inferior results from their pricing or governance approach.
- Use equivalent tasks and counterbalance order. Track setup separately from task
  time, correctness, negative/access coverage, repeat explanations and handoff time.
- Keep failed runs. Separate supplied demo fixtures from live observations and
  independent human trials. Publish methodology before any superiority claim.
- Update this dated review before public positioning or a release comparison.
  The current document is a research baseline, not a live market monitor.
