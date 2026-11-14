# Decision methods for a review

Use this to weigh a design, not to build one. State described: Winter '27 (API 68.0),
checked 2026-11-14. **verify** = not confirmed in a fetched primary source: verify in the
org or the current documentation.

## 1. The framework, and how to use it

Salesforce Well-Architected has five pillars since 2026-09-14: Trust, Reliability,
Operational Excellence, Resource and Cost Optimization, Fairness, each with pattern and
anti-pattern libraries [WA-FW] [WA-PAT]. It is a decision framework that "does not prescribe
a single right answer": the aim is the right quality profile for this client, not the
maximum on every pillar [WA-FW].

| Tension | The question for the review |
| --- | --- |
| Trust against cost | Is a control missing because it was judged too expensive, and who accepted that? |
| Reliability against cost | Are availability and recovery targets set from business impact, or is everything "critical"? |
| Operational Excellence against speed | Was a deploy or review step skipped for a deadline, and is it written down as debt? |
| Fairness against autonomy | Does an automated decision about people have a human check? |

- **Shared responsibility.** Salesforce secures and runs the platform. The client and its
  consultant own what is built on it: the access model, custom code, integrations,
  configuration, the restore strategy, monitoring, the ability to deploy [WA-T] [WA-R].
- **Proportion.** Operational Excellence describes three levels (lean, scale, governance
  optimised). A small org can sit at lean, with CLI deploys, declarative automation and
  native logs, if the trade-off is stated [WA-OE]. Judge a five-person nonprofit's org
  against lean, not against an enterprise pipeline.
- **Do not defer trust.** "Missing encryption or ungoverned integrations become costly
  retrofits", and permissive defaults set at first implementation are the commonest trust
  debt in older orgs [WA-T].

## 2. Compare options

1. List what the org already owns that could do the job. Building without that comparison is
   a named anti-pattern [P-ARCH].
2. Put two or three options side by side: standard, package, declarative, code.
3. For each: effort now, who maintains it, cost over three to five years with the
   assumptions written down, limit and volume risk, what the admin can change, how it is
   undone [WA-RC].
4. Take the simplest that meets the criteria and record why the simpler ones do not.

The decision tables are in `salesforce-build/references/choosing-the-tool.md` (automation,
forms, configuration storage, relationship type), `salesforce-build/references/access-and-sharing.md`
(sharing mechanism), `salesforce-build/references/apex-and-lwc.md` (asynchronous tools) and
`salesforce-build/references/integration.md` (integration pattern). The decision record
format is in `salesforce-delivery/references/design-and-estimate.md`. Decisions belong in
records kept with the source [P-CODE].

## 3. Walk one transaction

Pick the most common real case and the worst one. For each, write what happens at every
step and who or what does it:

| Step | Look for |
| --- | --- |
| The user opens a page | Which page, for which profile and record type; what loads first |
| The user is allowed to | Which permission set and which sharing give the access; what a user without them sees |
| The record saves | Every automation that fires, in order; what else each one writes |
| Data is written | Which objects, how many rows per save, what rolls up, what locks |
| Outside systems | What is called or published, when, with which identity; what the other side does on a repeat |
| It fails | Where it can fail; what the user sees; who is told; what is left half done |
| It runs again | A second save, a retry, a replay: duplicates or the same result? |
| At volume | 200 records in one transaction; the yearly total; the largest parent and owner |

A design that cannot be walked this way is not ready for review: say so.

## 4. Org strategy and packaging

- A single org is the default. Several orgs multiply licences, sandboxes, integration and
  governance and are "difficult and expensive to reverse" [WA-RC].
- Version control is the source of truth for anything bound for production; the org as the
  source of truth and ad hoc deployments are anti-patterns [P-DEVOPS]. For a small org,
  unpackaged metadata in source control is an acceptable lean position [WA-OE].
- In orgs dominated by managed packages (NPSP), sandbox-based development is the practical
  norm; scratch orgs suit unpackaged and unlocked-package work [P-DEVOPS].
- A managed package's components are not the client's to edit: extend through documented
  extension points, never copy or redeploy package components, retest after upgrades
  [WA-T]. Detail on which properties a subscriber may change: **verify** in the packaging
  guide.
- Technical debt is tracked with impact and estimate, improved in passing, and reviewed:
  automation with no runs in 90 days, jobs that always process nothing, superseded items
  never switched off. Each deactivation is recorded so it can be reversed [P-CODE] [WA-RC].

## 5. Reliability and operations

- **Monitoring an org should have**: Setup Audit Trail exported (it keeps 180 days), Health
  Check each quarter, error mail to a monitored address, failed and aborted jobs reviewed,
  limits watched with warnings at 70% and 85% [WA-R] [WA-OE] [P-OBS].
- **Alerts** have an owner and a response; alert on a sustained pattern, not a single error
  [P-OBS].
- **Backup**: platform infrastructure backups are not the client's to restore from. The
  native export keeps files 48 hours and has no point-in-time restore: a backup product is
  needed where recovery targets demand it, and "a backup that has never been restored is an
  untested assumption" [WA-R] [P-DR].
- **Deployment safety**: a validation deployment first; a low-traffic window; smoke tests
  straight after; a runbook with steps, checks, rollback and communication; a way back that
  was rehearsed [P-DEVOPS].
- **Risky change ships switched off** behind a custom metadata flag or a custom permission
  and is opened to a small group first [P-DEVOPS].
- **Degrade gracefully**: a failing supporting service must not block a critical
  transaction [P-AVAIL].

## 6. AI on the platform

- Use an agent only where reasoning is needed; a fixed path belongs in Flow or Apex.
  "Start at the minimal orchestration level that can achieve the goal" [DG-AGENT].
- An employee-facing agent runs as the signed-in user; a customer-facing agent runs as a
  dedicated least-privilege user, never a guest user or an administrator [P-TAG].
- The list of actions given to an agent is a permission boundary: anything listed can be
  reached through prompt manipulation. Remove what it does not need [P-TAG].
- Human approval for irreversible, financial, outward or regulated actions is a step in the
  workflow, outside the agent's reasoning, not an instruction in a prompt [P-TAG].
- Record text, tool output and retrieved documents are untrusted input [P-TAG].

## 7. What changed recently

Guidance older than these dates may be stale. Check the org's release before relying on any.

| Change | Since | Source |
| --- | --- | --- |
| Well-Architected restructured into five pillars; the Trusted, Easy, Adaptable pages are gone | 2026-09-14 | [WA-BLOG] |
| Apex at API 67.0: database operations in user mode by default, `with sharing` when no keyword, `WITH SECURITY_ENFORCED` removed; older classes unchanged until their version is raised | Summer '26 | [REL-S26] |
| Flow: retry on record locks, collection filtering without a loop; an option to keep manual shares on owner change | Winter '27 | [REL-W27] |
| New connected apps cannot be created; integrations register as external client apps | Spring '26 | [WA-T] |
| Username-password and user-agent sign-in flows retire | 2027-02-20 | [REL-W27] |
| Salesforce to Salesforce retires | Spring '27 | [REL-W27] |
| Process Builder end of support (it still runs) | 2025-12-31 | [WA-RC] |
| Salesforce's local developer MCP server reached end of life; its tooling is now published as agent skills | 2026-11-02 | [MCP-EOL] |
| Product names: Data Cloud is Data 360; Nonprofit Cloud is marketed as Agentforce Nonprofit | 2025 to 2026 | [WA-RC] |
| Older API versions and SOAP login are being retired in stages | **verify** dates | Salesforce Help |

Current decision guides: agentic or traditional workflow automation, record-triggered
automation, asynchronous processing, building forms, event-driven architecture, data
integration, and two for Data 360 [DG-LIST].

## Sources

- [WA-FW] https://architect.salesforce.com/docs/architect/well-architected/guide/framework.html
- [WA-BLOG] https://www.salesforce.com/blog/new-well-architected-framework/
- [WA-PAT] https://architect.salesforce.com/docs/architect/well-architected/guide/patterns-and-anti-patterns.html
- [WA-T] https://architect.salesforce.com/docs/architect/well-architected/guide/trust.html
- [WA-R] https://architect.salesforce.com/docs/architect/well-architected/guide/reliability.html
- [WA-OE] https://architect.salesforce.com/docs/architect/well-architected/guide/operational-excellence.html
- [WA-RC] https://architect.salesforce.com/docs/architect/well-architected/guide/resource-and-cost-optimization.html
- [P-ARCH] https://architect.salesforce.com/docs/architect/well-architected/guide/resource-and-cost-optimization-architecture-patterns
- [P-CODE] https://architect.salesforce.com/docs/architect/well-architected/guide/resource-and-cost-optimization-code-quality-patterns
- [P-DEVOPS] https://architect.salesforce.com/docs/architect/well-architected/guide/operational-excellence-devops-patterns
- [P-OBS] https://architect.salesforce.com/docs/architect/well-architected/guide/operational-excellence-observability-patterns
- [P-AVAIL] https://architect.salesforce.com/docs/architect/well-architected/guide/reliability-availability-performance-patterns
- [P-DR] https://architect.salesforce.com/docs/architect/well-architected/guide/reliability-disaster-recovery-patterns
- [P-TAG] https://architect.salesforce.com/docs/architect/well-architected/guide/trust-agentic-patterns
- [DG-AGENT] https://architect.salesforce.com/docs/architect/decision-guides/guide/determining-agentic-vs-traditional-workflow-automation
- [DG-LIST] https://architect.salesforce.com/decision-guides
- [REL-S26] https://www.salesforce.com/blog/summer-26-release-architect-highlights/
- [REL-W27] https://www.salesforce.com/blog/winter-27-release-architect-highlights/
- [MCP-EOL] https://github.com/forcedotcom/mcp/issues/46
