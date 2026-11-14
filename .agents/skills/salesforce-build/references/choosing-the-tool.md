# Choosing the tool

Use this when you turn a requirement into a design. Start at the top of each table and stop
at the first row that fits. State described: Winter '27 (API 68.0), checked 2026-11-14.
**verify** = not confirmed in a fetched primary source: verify in the org or the current
documentation. field-reported = seen in client work, usually one org. Choices here are
judgements: record the reason with the change.

## 1. Write the native baseline first

Before any design, write three lines:

1. What the org already has that does part of the job: standard features, installed
   packages, the connector's own mapping, existing fields, flows, rules and rollup tools.
2. The simplest design that uses only those plus configuration, and what it cannot do.
3. Its effort next to what the client was told.

Add an object, code or a staging layer only for a gap you can name, and ask first
(field-reported: a design with a staging object, Apex and platform events was rejected as
over-engineered, and the native redesign cut the effort by about two thirds). Building
without weighing what the org owns and the packages that exist is a named anti-pattern
[P-ARCH]. In a nonprofit org the product's model is the baseline: skills `salesforce-npsp`
and `salesforce-nonprofit-cloud`.

Simple does not mean skipping the access baseline: a dedicated least-privilege integration
user was cut from one design as too much and the client's own admin asked for it ten days
later (field-reported).

## 2. The ladder

| Step | Use it when | The client's admin can change it |
| --- | --- | --- |
| 1. Standard feature or setting | The platform or an installed package already does it | Yes, in Setup |
| 2. Formula field, default value, roll-up summary, validation rule, duplicate rule | A value is derived, totalled or checked on one record or its children | Yes, in Setup |
| 3. Before-save record-triggered flow | Fields on the record being saved must be set | Yes, in Flow Builder |
| 4. After-save, scheduled or screen flow | Other records, actions, email, timed work or guided input | Yes, in Flow Builder |
| 5. Flow that calls invocable Apex | One step needs computation or volume a flow handles badly | Partly: the flow, not the class |
| 6. Apex trigger or class, Lightning web component | Section 7 applies | No: a developer changes it |

Each step up costs the client maintenance: "Maintenance typically consumes 60-80% of
development capacity for mature solutions" [WA-RC]. Declarative automation is transparent to
admins, and declarative-only designs do reach performance and maintainability limits
[WA-OE], so the ladder is a default with reasons, not a ban on code.

Check: the description or decision record names the step chosen and what the step below it
could not do.

## 3. Automation for a record change

Source: [DG-RT]. Count the automations on the object first (flows, triggers, processes).

| Situation | Use |
| --- | --- |
| Set fields on the record being saved, any volume | Before-save record-triggered flow (or an Apex before trigger where one already owns the object) |
| Low density: under 15 automations, 1 to 200 records, 0 to 1 downstream writes | Record-triggered flow |
| Medium density: 15 to 30 automations, 2 to 4 downstream writes, recursion risk | Flow for entry conditions and orchestration, plus invocable Apex |
| High density: over 30 automations, 2,000 to 10,000 or more records, 5 or more downstream writes | Apex trigger framework driven by custom metadata |
| Savepoints, rollback, partial success, field-specific errors | Apex |
| Action at a time relative to a date on the record | Flow scheduled path |
| Email alerts and simple notifications | Flow |
| After undelete | Apex trigger |
| Heavy follow-on work after save | Change Data Capture subscriber, or a pending flag swept by a scheduled job |
| A fixed path proposed as an AI agent | Flow or Apex: an agent for a path with no reasoning is an anti-pattern [DG-AGENT] |

- "Use one entry point per Salesforce Object": follow the mechanism the object already uses.
- Prefer one automation that sets the value at creation to a second one that corrects it
  later (field-reported).
- Nothing new in Workflow Rules or Process Builder.

Details for flows: `salesforce-flow/references/build-checklist.md`.

## 4. Forms and user input

Source: [DG-FORMS].

| Need | Use |
| --- | --- |
| Create, edit or view one object on a record page, with fields shown by condition | Dynamic Forms |
| Several screens (a guided wizard) | Screen flow |
| Custom events, form-specific validation, unit tests, fine control of style | Lightning web component, alone or inside a screen flow |

"If you can get all of your user inputs from a single-screen form, start with Dynamic Forms."
Dynamic Forms cannot make a field required or read-only by condition and are not available
on Experience Cloud pages. Decide between a screen flow and a component before you build:
building both and letting QA choose doubles the work (field-reported).

## 5. Where configuration lives

Sources: [F-BASICS] [DG-RT] [P-DATA]. The row choices are judgement.

| What | Store it in | Why |
| --- | --- | --- |
| Rules, mappings, thresholds and switches that should move with deployments | Custom metadata type, each record with a description | It is metadata: deployed, packaged and copied to every sandbox |
| Values that differ per user or profile, or that an admin flips in one org | Hierarchy custom setting | It is data: not deployed, different in each org |
| Who may use or skip a feature | Custom permission in a permission set | Checked in flows (`$Permission`), validation rules and Apex; assigned per person |
| Endpoints and secrets | Named credential with an external credential | Rotated without a code change; access by permission set |
| Text a user reads | Custom label | Deployable and translatable |
| Reference data the business owns and reports on | Records of a custom object | Data with sharing, history and reports |
| Never | Ids or values in formulas, flows or code; secrets in settings or custom metadata | Named anti-patterns [P-AUTO] |

- A custom setting's values do not travel with a deployment, and after a sandbox refresh they
  come back as production's values (field-reported). List them in the post-refresh steps.
- A bypass for loads or integrations is a custom permission, not a profile name: "The modern,
  scalable standard for implementing bypass logic is custom permissions" [DG-RT].

Check: `[check]` no Id literal, host name, user name or profile name in the change; each new
custom metadata record has its description filled.

## 6. Relationship type

Sources: [TH-DM] [WA-RC]; limits are **verify** (Salesforce Help).

| Question | Lookup | Master-detail |
| --- | --- | --- |
| Can the child exist without the parent? | Yes | No |
| Child ownership and sharing | Its own | Inherited from the parent |
| Deleting the parent | Read the field's delete setting (`deleteConstraint`, **verify**) | Children are deleted |
| Roll-up summary on the parent | No: use a flow, Apex or the package's rollup tool | Yes |
| Limit per object | 40 relationships in total (**verify**) | 2 (**verify**) |

Use master-detail only when the child should not exist without the parent. Converting one to
the other later is a stop-and-ask change. Check: `type` in the field file is `Lookup` or
`MasterDetail`; Setup > Object Manager > the object > Object Limits.

## 7. Record type or separate object

Judgement; no Salesforce page was confirmed for this in this round.

| The two kinds of record differ in | Lean to |
| --- | --- |
| Picklist values, page layout or process only | Record type |
| Fields, relationships, who may see them, or how long they are kept | Separate object |
| Which fields show, by a value on the record | Neither: field visibility on the Lightning page |

A new record type also needs its picklist values, its page assignment and its assignment to
users (field-reported: a restricted picklist value missing from the record type made
integration writes fail). Read what the org's existing record types control before you add
one.

## 8. When code is right, and what the reason must say

| A reason that holds | Source |
| --- | --- |
| The transaction needs savepoints, rollback or partial success | [DG-RT] |
| The work runs after undelete, where a flow cannot call invocable actions | [DG-RT] |
| The object is at high automation density, or thousands of records arrive per transaction | [DG-RT] |
| One step is compute-heavy: put that step in invocable Apex and keep the flow | [DG-RT] |
| The form needs custom events, form-specific validation, unit tests or fine control of style | [DG-FORMS] |
| A callout that External Services or a flow HTTP callout cannot make | [F-INT] |

Reasons that do not hold: it is quicker to generate, it is more flexible, it works around a
limit a better design avoids. "Introduce Apex triggers only when absolutely necessary" [DG-RT].

The recorded reason says:

1. The requirement, in one sentence.
2. Each simpler step tried, in ladder order, and the named limit or measured result that
   ruled it out.
3. Who will maintain the code, and that the client agreed to own code.
4. What the admin can still change without a developer (custom metadata, labels).
5. The tests that prove it: bulk, negative and as the intended user.

Put it in the class header comment and in the decision record. Choosing code where
configuration could do the job is a stop-and-ask point. Then follow `apex-and-lwc.md`.

## Sources

- [DG-RT] https://architect.salesforce.com/docs/architect/decision-guides/guide/record-triggered
- [DG-FORMS] https://architect.salesforce.com/docs/architect/decision-guides/guide/build-forms
- [DG-AGENT] https://architect.salesforce.com/docs/architect/decision-guides/guide/determining-agentic-vs-traditional-workflow-automation
- [F-BASICS] https://architect.salesforce.com/docs/architect/fundamentals/guide/architecture-basics
- [F-INT] https://architect.salesforce.com/docs/architect/fundamentals/guide/integration-patterns
- [WA-OE] https://architect.salesforce.com/docs/architect/well-architected/guide/operational-excellence.html
- [WA-RC] https://architect.salesforce.com/docs/architect/well-architected/guide/resource-and-cost-optimization.html
- [P-ARCH] https://architect.salesforce.com/docs/architect/well-architected/guide/resource-and-cost-optimization-architecture-patterns
- [P-AUTO] https://architect.salesforce.com/docs/architect/well-architected/guide/operational-excellence-automation-patterns
- [P-DATA] https://architect.salesforce.com/docs/architect/well-architected/guide/trust-data-protection-patterns
- [TH-DM] https://trailhead.salesforce.com/content/learn/modules/data_modeling/objects_intro
- **verify** items: relationship limits (https://help.salesforce.com/s/articleView?id=platform.relationships_considerations.htm&type=5).
- field-reported: lessons from real client work with Torque, not re-tested for this release.
