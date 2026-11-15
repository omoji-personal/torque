# Solution design, decision records, estimates and scope

Part A is for choosing and recording a design. Part B is for estimates and scope control.
State described: Winter '27 (API 68.0), checked 2026-11-14. field-reported = seen in client
work, usually one org. `[HUMAN]` = a person decides or acts.

## A. Solution design

### A1. Checklist

1. Before design work or a review round, write an options table in this order: standard
   feature, feature of an installed package, declarative, code, with rough hours and what
   the client was told beside the total
   (`salesforce-build/references/choosing-the-tool.md`, section 1). Take the first option
   that meets the criteria and say in one line why the earlier ones fail. For a small
   ticket the table is optional: one line on the option taken is enough.
2. For each significant requirement keep at least two options with their trade-offs in
   front of the client. "Going beyond what works to what's best is the real mark of an
   advocate" [TH-ADV].
3. Compare standard, package and custom on cost over three to five years "with documented
   assumptions". "Optimize for long-term value, not short-term cost minimization"; a package
   brings subscription fees, custom work brings maintenance [WA-RC].
4. Choose tools from the decision tables (`salesforce-build/references/choosing-the-tool.md`):
   automation by density, one entry mechanism per object, before-save where possible.
5. Draft the data model change: objects, fields, relationships, external Ids, volumes, skew
   (`salesforce-build/references/data-model.md`).
6. Draft access closed first: private where data is sensitive, permission sets and groups by
   persona, field access by classification, one identity per integration
   (`salesforce-build/references/access-and-sharing.md`).
7. Check limit headroom and bulk behaviour against a stated target. Default: "well under
   half" [WA-RC]; 70% of a limit [WA-R] is the outer bound, with the reason recorded.
8. Plan the bypass for loads and integrations now: a custom permission [DG-RT].
9. Test maintainability: can the client's admin open it, understand it from its descriptions
   and change its settings without a developer?
10. Write a decision record for each consequential choice (A2). Update diagrams in
    Salesforce's notation, each with a title and a stated scope [DIAGRAMS].
11. Note the test approach and the migration impact for each design element.
12. `[HUMAN]` a second person reviews the design. Schema changes need a named owner: "No
    changes should be made without reviewing the impact to the organization's data" [COE].
13. `[HUMAN]` the client owner approves schema, access model, estimate and assumptions.

### A2. The decision record

Write one when the choice is hard to reverse, costs the client maintenance, or trades one
quality against another: code where configuration could serve, a new object, a relationship
type, a sharing model, an integration pattern, a package, anything on a stop-and-ask list.

| Part | Content |
| --- | --- |
| Title | The decision in a few words, with a number |
| Context | The requirement, the constraints, the facts from the org (with org and date) |
| Options | Each option considered, in ladder order, and why it was not chosen |
| Decision | What was chosen |
| Consequences | What gets easier, what gets harder, what the client now maintains, how to undo it |
| Status | Proposed, accepted (by whom, when), or superseded by which record |

Keep it to one or two pages, in the repository or the client's records, and keep superseded
records [ADR]. Well-Architected asks for decisions recorded this way [WA-T] [WA-RC].

Example, shortened: "DR-004 Renewal date set by flow, not Apex. Context: renewal date must
follow Start Date plus Term on save; 3 automations on Membership, loads under 200 rows.
Options: formula field (rejected: staff must be able to override the date); before-save flow
(chosen); Apex trigger (rejected: nothing here needs code, and the client has no developer).
Consequences: the admin edits the flow; the override needs a checkbox field. Accepted by the
operations lead."

### A3. When to stop and ask

A change trades one pillar against another (speed against access control, cost against
recovery); a target such as availability, retention or a compliance figure would have to be
invented; the framework and the client's own convention disagree.

### A4. Field lessons

- A design with a staging object, Apex and platform events passed seven review rounds while
  its estimate nearly tripled; the native redesign cut the effort by about two thirds. Review
  rounds do not test proportion: compare with the baseline first.
- Present a decision that is the client's or the consultant's one at a time, with two to four
  concrete options and a recommendation, not as a list at the end of a report.
- A feasibility question about financial or accounting configuration gets a written proposal
  (the exact node, the current and proposed expression, the effect, what does not change,
  risks, test plan, rollback), not a change, not even in a sandbox.
- Price the tier the design needs at the client's record volume, and cost every option on
  one basis.
- Do not promise technical specifics in a proposal: mark them "to confirm in discovery".

### A5. Artifacts

| Artifact | Minimum contents |
| --- | --- |
| Solution design | Context, options and choice, data model, automation, access, integrations, limit headroom, test and migration impact |
| Decision record | A2 |
| Access design | Sharing defaults, permission sets and groups by persona, field access, integration users |
| Draft data dictionary | Object, field, type, description, classification, source, owner |

Fails when: custom is built where standard fits; several automation tools sit on one object;
the client's admin cannot maintain the design; broad permissions are designed in; no decision
is recorded; the estimate has no assumptions.

## B. Estimating and scope

### B1. Checklist

1. Estimate per story or phase, with assumptions, exclusions, dependencies and confidence.
   Smaller stories estimate better [TH-STORY]. Revisit the estimate as design decisions land
   [WA-RC].
2. Count all the work: discovery, data preparation, testing, UAT support, deployment,
   training, documents and the support period after go-live. Data preparation dominates a
   migration ("About 99% of the time you spend on an import will be making sure your data is
   ready outside Salesforce" [TH-IMPORT]).
3. Treat the signed backlog as the scope baseline and keep it in version control.
4. Before you polish a plan, compare its effort with what the client was told. Take any gap
   to whoever owns the client relationship before you build (field-reported).
5. Track actual effort per story from the session records.
6. At each checkpoint compare actual with estimate and flag variance beyond the agreed
   tolerance.
7. A new request or a changed criterion becomes a change request: logged, with its impact on
   scope, time, cost and risk [APM]. `[HUMAN]` it is approved, rejected or deferred before
   work starts.
8. Gold-plating check: every built item traces to an accepted criterion. "If a story does not
   have value, it should not be created" [TH-STORY].
9. Stop and re-scope when an assumption fails, variance passes tolerance, or discovery finds
   a different problem. `[HUMAN]` the re-scope is agreed with the client in writing.

An alternative to an open-ended estimate is a fixed budget with variable scope: agree the
effort the outcome is worth first, then shape the design to fit it.

### B2. Field lessons

- An agent's own hour figures are arithmetic, not evidence: they ran two to four times a
  delivery lead's and were withdrawn. Hours shown to a client come from the person
  accountable for the estimate; say when a figure has not been checked against past jobs.
- Agent speed applies to build work (configuration, flows, loads, documents, test scripts).
  Discovery, decisions, UAT, training and screen-only setup run at human pace: do not price a
  job on savings that depend on them.
- Fix the stopping rule of a review loop before it starts (new findings per pass ran 7, 36,
  14 in one loop). Items the consultant leaves open stay open.
- "Simplify" means fewer documents and no speculative machinery, not dropping what the work
  was for.

### B3. Artifacts

| Artifact | Minimum contents |
| --- | --- |
| Estimate and assumptions log | Items, effort, assumptions, exclusions, dependencies, confidence |
| Scope baseline | The signed backlog, with version and date |
| Change request log | Request, date, impact on scope, time, cost and risk, decision, approver |
| Re-scope note | What changed, options, the decision, who agreed |

Fails when: discovery has no boundary; scope grows through "small" requests; the estimate
omits data preparation, testing, training, deployment and support; work goes beyond accepted
criteria; work continues after a key assumption failed.

The agent can keep the assumption log, compare actuals with the estimate, flag work that maps
to no accepted criterion and draft change requests. A person decides price, commitments,
approvals and the re-scope conversation.

## Sources

- [TH-ADV] https://trailhead.salesforce.com/content/learn/modules/business-analyst-best-practices/advocate-for-the-customer
- [TH-STORY] https://trailhead.salesforce.com/content/learn/modules/user-story-creation/construct-a-user-story
- [TH-IMPORT] https://trailhead.salesforce.com/content/learn/projects/import-your-data-using-npsp-data-importer/prepare-your-data-for-import
- [WA-T] https://architect.salesforce.com/docs/architect/well-architected/guide/trust.html
- [WA-R] https://architect.salesforce.com/docs/architect/well-architected/guide/reliability.html
- [WA-RC] https://architect.salesforce.com/docs/architect/well-architected/guide/resource-and-cost-optimization.html
- [DG-RT] https://architect.salesforce.com/docs/architect/decision-guides/guide/record-triggered
- [DIAGRAMS] https://architect.salesforce.com/diagrams
- [COE] https://www.salesforce.com/blog/center-of-excellence-ai-governance/
- [ADR] https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions
- [APM] https://www.apm.org.uk/resources/what-is-project-management/what-is-change-control/
- field-reported: lessons from real client work with Torque and its predecessor (one
  consultant, about a dozen clients, mostly sandboxes): working notes, an after-action review
  of 166 rework and defect episodes, and a classification of 273 failed Salesforce CLI calls.
  Not re-tested for this release.
