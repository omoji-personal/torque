# Take over an org, discovery and requirements

Part A is for the first days in an org you did not build. Part B is for turning what people
say into requirements you can build and test. State described: Winter '27 (API 68.0),
checked 2026-11-14. **verify** = not confirmed in a fetched primary source. field-reported =
seen in client work, usually one org. `[HUMAN]` = a person decides or acts.

## A. Take over or assess an org

"The moment you get access is the most un-informed you will ever be about this work"
(community checklist [BEN-INHERIT]). Everything here is read-only: SELECT queries and
metadata retrieves. No test runs, anonymous Apex, deploys or clicks that save
(field-reported: consultants asked for exactly this mode in three engagements).

### A1. Checklist

1. Record sponsor, product owner, client admin and who approves production changes, coded,
   in the private workspace.
2. Draft the access request: a named user, multi-factor sign-in, permission sets. Say that
   listing metadata needs Modify Metadata Through Metadata API Functions or Modify All Data,
   so even a read-only assessment asks for one elevated permission [PLUGIN-ORG]. `[HUMAN]`
   the client grants access.
3. Resolve the org: `sf data query --target-org ALIAS --query "SELECT Id, Name, IsSandbox,
   OrganizationType FROM Organization"`. Record the Id and type. "Not a sandbox" is not
   "production": a Developer Edition org reports `IsSandbox` false [ADV]. Keep CLI
   credential output out of notes.
4. Limits and volumes, with the date: `sf org list limits --target-org ALIAS` and
   `sf org list sobject record-counts --target-org ALIAS`. The counts are approximate: label
   them so [PLUGIN-LIMITS].
5. Baseline: `sf project generate manifest --from-org ALIAS`, retrieve into a private
   repository, commit. Packaged metadata is left out unless you pass `--include-packages`:
   note the exclusion [PLUGIN-DR]. Scan the retrieved copy for secrets before you open
   files (field-reported).
6. Installed packages with versions: `sf package installed list --target-org ALIAS`. Each is
   part of the trust boundary [WA-T].
7. Automation per object: count flows, triggers, Process Builder processes and workflow
   rules; classify density (`salesforce-flow/references/build-checklist.md`, step 1).
8. Security posture: read the Health Check score and its high-risk items. Change nothing:
   "Fix Risks" can break integrations or remove access [TH-HC].
9. History: export Setup Audit Trail now. It keeps 180 days [WA-OE].
10. Licences, inactive users, integrations and app registrations, storage [WA-RC].
11. Backup: does one exist, and when was a restore last tested? The native export runs every
    7 days on Enterprise, Performance and Unlimited editions and every 29 days on others, and
    keeps files 48 hours [WA-R].
12. Draft the assessment: dated facts, risks, unknowns, first actions.
13. `[HUMAN]` the client admin fact-checks it; the sponsor accepts scope and priorities.

Optional, ask first: Org Check, a Salesforce Labs tool (not a supported product) that scores
technical debt, where Salesforce Optimizer is no longer available [ORGCHECK].

### A2. Debt signals with published thresholds

| Signal | Threshold | Source |
| --- | --- | --- |
| Automations on one object | Under 15 low, 15 to 30 medium, over 30 high | [DG-RT] |
| Flow and Apex triggers both entering one object, uncoordinated | Any | [DG-RT] |
| One flow holding all of an object's logic | Any | [DG-RT] |
| Process Builder processes still active | Any: end of support was 2025-12-31 | [WA-RC] |
| Automation with no runs in 90 days | Candidate for removal | [WA-RC] |
| One parent with more than 10,000 children | Data skew | [WA-RC] |
| Custom fields and flows with no description | Count them | field-reported: over 40% of one org's active flows |

### A3. Field lessons

- A sandbox is a dated copy. State a production fact only from production, read that day.
- Check what is already set up before you say it is missing or build a second one
  (field-reported: an hour of reading documents by browser while an API connection existed).
- For a study of a full sandbox or production, use COUNT and GROUP BY; record bodies stay out
  of the session.
- Opening a package's settings page can write (NPSP Settings inserted handlers and jobs):
  see skill `salesforce-npsp`.
- Where sources from two product generations conflict, tag each fact with its generation and
  keep one verified-facts file that wins.

### A4. Artifacts

| Artifact | Minimum contents |
| --- | --- |
| Stakeholder map | Role, interest, influence, decision rights, contact cadence |
| Access and environment register | Org Id, type, purpose, refresh date, preview status, owner, data sensitivity; per person: permissions, sign-in method, start and end, approver |
| Org assessment | Edition and limits, licences, packages, metadata counts, automation per object and density, security posture, data volumes, debt signals, risks, unknowns, first actions; every figure dated |
| Baseline record | Commit, manifest scope, exclusions, record counts, limits, Health Check score, backup date |

Fails when: work lands in the wrong org; a setting is changed while "just looking"; findings
come from an administrator's view only; approximate counts are reported as exact; there is
no baseline, so later drift cannot be explained.

## B. Discovery and requirements

### B1. Checklist

1. Prepare: read earlier documents and the assessment; list stakeholders and questions.
2. `[HUMAN]` run workshops and interviews, recorded with consent. Map processes live with the
   people who do them: "If anyone is missing, it invalidates the workshop" [TH-BPM].
3. Write up within a day: decisions, open questions, the owner of each next step [TH-SCHED].
4. Draw the process as it is and as it will be; attach requirements to steps.
5. Write stories as role, goal and benefit, and test each with INVEST: "If the user story
   doesn't meet one of the checks, it probably needs a rewrite" [TH-STORY].
6. Add acceptance criteria: "a set of statements, each with a clear pass/fail result" that
   "should state intent, but not a solution"; at least one per story, each testable on its
   own [TH-STORY].
7. Non-functional pass before design: volumes, response, availability, security and data
   classification, accessibility (WCAG 2.2 AA [WA-F]), privacy, integrations.
8. Fit-gap each story: standard, configuration, package, custom or not feasible, with a
   documentation link per row. Offer a choice: "Bring your customer a choice of solutions"
   [TH-ADV].
9. Resolve every object, field and value the stories name against the org; mark anything
   not confirmed there as proposed, not existing.
10. Start the traceability matrix: requirement, story, component, test.
11. State the success measure and how it will be read, as a number and a date [TH-LEVERS].
12. Commit requirements and stories to version control [TH-REQ].
13. `[HUMAN]` stakeholders confirm the write-up; the product owner approves backlog order
    and the scope baseline.

### B2. Why before how

"Just taking an order is like setting a little trap for someone else to discover later", and
"you must finish understanding a problem before you begin to solve for it" [TH-ADV]
[TH-SCHED]. Read a request for what is asked, not for what is merely described as pain; do
not add scope nobody asked for; do not guess a default and present it as the client's
decision.

### B3. Field lessons

- Each open question names who answers it and what the answer unlocks, lives in one place,
  and is asked only after an evidence check (field-reported: one check cut twenty questions
  to nine and corrected several stated facts).
- A meeting recorder's summary is not the meeting: it was wrong in half of a sample. Check
  the transcript, and cite a transcript time for each decision.
- Examples in a brief are illustrations, not the scope. Say the scope back in one line
  before you start.
- For a new lead or a vague ask, write one page first: what is known, what is not, a working
  estimate with its basis, what the decision-maker must settle. No kits or playbooks yet.
- Do not assume there is a Salesforce org: discovery may start from another system, an
  export, chats and emails.

### B4. Artifacts

| Artifact | Minimum contents |
| --- | --- |
| Discovery record | Date, attendees by role, decisions, open questions, owner of each next step |
| Process map | Steps with role, inputs and outputs, linked requirements |
| Requirements register | Id, statement, source, priority, status, linked stories |
| User story | Role, goal, benefit, acceptance criteria (pass or fail), non-functional notes, links |
| Non-functional list | Volumes, response, availability, security and classification, accessibility, privacy, integrations |
| Fit-gap matrix | Requirement, standard feature or gap, option chosen, evidence link, effort class |
| Traceability matrix | Requirement, story, component, test, release |
| Open-questions log | Question, who answers, what it unlocks, evidence checked, answer, date |

Fails when: the team takes orders; a stakeholder group is missing; criteria prescribe a
solution; non-functional needs surface at UAT; requirements live in chat; "adoption" is never
defined.

The agent can draft stories from notes, test them against INVEST, find gaps and
contradictions, map stories to existing components and keep the matrix. A person decides who
is a stakeholder, what the problem is, priority, scope, and confirms intent with the client.

## Sources

- [PLUGIN-ORG] https://raw.githubusercontent.com/salesforcecli/plugin-org/main/README.md
- [PLUGIN-LIMITS] https://raw.githubusercontent.com/salesforcecli/plugin-limits/main/README.md
- [PLUGIN-DR] https://raw.githubusercontent.com/salesforcecli/plugin-deploy-retrieve/main/README.md
- [ORGCHECK] https://salesforcelabs.github.io/OrgCheck/
- [DG-RT] https://architect.salesforce.com/docs/architect/decision-guides/guide/record-triggered
- [WA-T] https://architect.salesforce.com/docs/architect/well-architected/guide/trust.html
- [WA-R] https://architect.salesforce.com/docs/architect/well-architected/guide/reliability.html
- [WA-OE] https://architect.salesforce.com/docs/architect/well-architected/guide/operational-excellence.html
- [WA-RC] https://architect.salesforce.com/docs/architect/well-architected/guide/resource-and-cost-optimization.html
- [WA-F] https://architect.salesforce.com/docs/architect/well-architected/guide/fairness.html
- [TH-HC] https://trailhead.salesforce.com/content/learn/modules/security_basics/security_basics_healthcheck
- [TH-BPM] https://trailhead.salesforce.com/content/learn/modules/business-process-mapping/see-business-process-mapping-in-practice
- [TH-STORY] https://trailhead.salesforce.com/content/learn/modules/user-story-creation/construct-a-user-story
- [TH-ADV] https://trailhead.salesforce.com/content/learn/modules/business-analyst-best-practices/advocate-for-the-customer
- [TH-SCHED] https://trailhead.salesforce.com/content/learn/modules/business-analyst-best-practices/schedule-for-success
- [TH-LEVERS] https://trailhead.salesforce.com/content/learn/modules/levers-of-change-model/use-the-levers-model-to-diagnose-barriers-to-change
- [TH-REQ] https://trailhead.salesforce.com/content/learn/modules/salesforce-business-analyst-certification-prep/review-requirements
- [BEN-INHERIT] Community checklist for an inherited org: https://www.salesforceben.com/inheriting-a-salesforce-org/
- [ADV] Torque advisory catalogue, entry `developer-edition-is-not-sandbox` (verified live): `torque advisory catalogue`
- field-reported: lessons from real client work with Torque and its predecessor (one
  consultant, about a dozen clients, mostly sandboxes): working notes, an after-action review
  of 166 rework and defect episodes, and a classification of 273 failed Salesforce CLI calls.
  Not re-tested for this release.
