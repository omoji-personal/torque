# Documentation, training, hand-over, support and org health

State described: Winter '27 (API 68.0), checked 2026-11-14. **verify** = not confirmed in a
fetched primary source: verify in the org or the current documentation. field-reported =
seen in client work, usually one org. Queries marked "lead" were not run for this release.
`[HUMAN]` = a person decides or acts.

## A. Documentation, training and hand-over

### A1. Checklist

1. Generate the data dictionary from the metadata, not by hand: object, field, type,
   description, help text, classification, what writes it. Blank descriptions show as gaps.
2. Write the admin guide (A2).
3. Write process documents and role guides in plain language, by task, and walk each step in
   the interface as that role. A step says where its action is found.
4. Assemble the decision log and the list of open items.
5. Draft in-app guidance for the key moments: one message, one audience, one purpose
   [TH-ENG].
6. Plan training after the build is stable: audience, format, date, local champions who can
   train others. Leaders need it too [TH-ADOPT]. Train in a stable sandbox with synthetic or
   anonymised data [WA-OE].
7. Fix the adoption measure as use of the process, with a number and a date, not as logins.
   Capture the baseline before go-live [TH-LEVERS].
8. Draft the communications: what changes, why, when, where to get help.
9. `[HUMAN]` one real user per role reads the guide and does the task.
10. `[HUMAN]` hand-over session with the client's admin, with a teach-back: the admin makes
    one real change from the guide, unaided. Agree when the support period after go-live
    ends.
11. `[HUMAN]` the client accepts documentation and training in writing.
12. Close access: revoke sessions and tokens in the org (`sf org logout` only deletes the
    local authorization), deactivate the user, remove local data, confirm in writing.

### A2. The admin guide

Written for the client's own administrator, in Setup terms:

| Section | Content |
| --- | --- |
| What exists | Objects, fields, flows, rules, permission sets, pages, reports, by purpose |
| Settings you can change | Each threshold, recipient, mapping and switch: where it lives (custom metadata record, custom setting, label) and how to change it without a deployment |
| Access | Each permission set and group, what it grants, who should hold it, how to assign it |
| Automation | Each flow and scheduled job: what starts it, what it does, how to see which version is active, where failures show, how to pause it and what pausing stops |
| Routine tasks | The few things an admin will do monthly, step by step |
| Known limits | What it does not do, by design |
| Decisions | The decision records, so the admin knows why |
| Contacts | Who to ask for what |

Record each deactivation so it can be reversed [WA-RC].

### A3. Field lessons

- Check a guide against the build: labels, limits, messages and removed components in the
  guide match the metadata.
- Before a tester or user gets a guide: dry-run it end to end, confirm their user is active
  and can see the new field, and that the named test data and report exist.
- Look at the rendered document: a pasted guide had broken formatting while its text export
  matched.
- A deliverable claims nothing that was not done, and says nothing undefined is designed.

### A4. Artifacts

| Artifact | Minimum contents |
| --- | --- |
| Data dictionary | Object, field, type, description, classification, source, owner |
| Admin guide | A2 |
| User guide or cheat sheet | Task steps by role in plain language, screens, who to ask |
| Training plan | Audience, format, schedule, materials, trainers, follow-up |
| Hand-off note | State (prepared, executed, verified), source locations, how to reproduce checks, open items and leftovers, next action. `torque handoff` renders the session history |

Fails when: documents describe the build and not the work; they are written for consultants;
no admin can maintain the system; adoption is counted as logins; training comes before the
build is stable; nothing happens after go-live.

## B. Support

### B1. Checklist

1. Intake: reporter, symptom, impact, urgency, org, time. Classify severity: 1 critical
   function, all users, data loss or security; 2 a critical function degraded; 3 limited
   impact [WA-OE].
2. Find the evidence inside the data boundary: the error email, the failed flow interview,
   the log.
3. Correlate with recent deployments, Setup Audit Trail and the platform release.
4. Check Salesforce Known Issues before you treat it as a local defect [TH-RELTEST].
5. State the cause, both the immediate one and the one in the process, and the fix options
   with their risk. Before you accept "the release broke it", compare failing and succeeding
   runs by owner, run-as user and flow version (field-reported).
6. `[HUMAN]` approve the fix and its route.
7. Fix in a sandbox, test with one neighbouring path, deploy through the normal route. A fix
   made by hand in production goes back into source the same day.
8. Verify in production as the affected user; `[HUMAN]` the reporter confirms.
9. Record cause, fix, verification and prevention. Tell the client in plain words: the
   cause, how many records or users over what period, the fix, and any past records the fix
   does not repair.

Do not widen access, relax a validation rule or add a catch-all fault handler in place of
the cause. Leave unrelated defects you find alone and list them.

### B2. After a severity 1 or 2 incident

Within 24 to 48 hours write the review: timeline, impact, causes, what worked, actions with
owners [WA-OE]. "You can't 'fix' people, but you can fix systems and processes" [SRE].

## C. Org health, each quarter

| Area | Read | Target or signal |
| --- | --- | --- |
| Security | Health Check score and failed high-risk settings | 80% or higher; each exception written down [WA-T] |
| Limits | `sf org list limits --target-org ALIAS`; storage by object | Warnings at 70% and 85% of an allocation [WA-RC] |
| Licences and users | Licences used against bought; inactive users | Reviewed "on a cadence, not just at renewal" [WA-RC] |
| Automation that fails | Setup > Paused and Failed Flow Interviews (**verify** the page name); lead: `SELECT ApexClass.Name, Status, NumberOfErrors, CompletedDate FROM AsyncApexJob WHERE Status IN ('Failed','Aborted') AND CreatedDate = LAST_N_DAYS:7` | None unexplained |
| Automation nobody uses | Flows and jobs with no runs in 90 days; remaining Process Builder processes | Removal or migration candidates [WA-RC] |
| Scheduled jobs | Lead: `SELECT CronJobDetail.Name, State, NextFireTime, CreatedBy.IsActive FROM CronTrigger` | Every job owned by an active user |
| Error mail | Where flow and Apex error mail goes | A monitored address, not the last person who edited a flow (**verify** the setting in Process Automation Settings) |
| Backup | Date of the last export; date of the last restore test | A restore tested each quarter; "a backup that has never been restored is an untested assumption" [WA-R] |
| Drift | Production against the repository | Explained differences only [WA-OE] |
| Documentation | Share of active flows and custom fields with no description | Falling |
| Debt register | Item, impact, cost to fix, workaround, owner, decision, review date | Reviewed with the client owner |

Write the report as dated findings by area, the trend since last time, recommended actions
and the owner's decisions. `[HUMAN]` the client owner agrees priorities. A review that lists
findings and changes nothing has failed.

## D. Platform and package releases

Each Salesforce release is a regression event for the client [TH-RELTEST]. Three times a
year:

1. Put the release dates in the calendar. The sandbox preview starts about 4 to 6 weeks
   before production [TH-PREVIEW].
2. Filter the release notes by "Enabled for Users" and "Admins must Enable" [TH-RELSTRAT].
3. In a preview sandbox test Setup > Release Updates, custom code, the high-value and
   high-use processes, and both sides of each integration [TH-RELTEST].
4. Tell users about changes that switch on by themselves before they notice [TH-RELSTRAT].

For a small org, "An hour or two is really all you need" [TH-RELSTRAT]. NPSP orgs have a
second cadence: package releases reach sandboxes first and production two weeks later
[TH-NPSPREL]. API versions and sign-in methods also retire: list which each integration
uses (**verify** the current dates).

## Sources

- [TH-ENG] https://trailhead.salesforce.com/content/learn/modules/user-engagement/design-a-user-engagement-journey
- [TH-ADOPT] https://trailhead.salesforce.com/content/learn/modules/salesforce-adoption-strategies/deploy-your-salesforce-technology
- [TH-LEVERS] https://trailhead.salesforce.com/content/learn/modules/levers-of-change-model/use-the-levers-model-to-diagnose-barriers-to-change
- [TH-RELTEST] https://trailhead.salesforce.com/content/learn/modules/advanced-salesforce-release-readiness-strategies/test-new-features-before-a-release
- [TH-RELSTRAT] https://trailhead.salesforce.com/content/learn/modules/sf_releases/sf_releases_strategy
- [TH-PREVIEW] https://trailhead.salesforce.com/content/learn/modules/sf_releases/sf-releases-sandbox-preview
- [TH-NPSPREL] https://trailhead.salesforce.com/content/learn/modules/nonprofit-success-pack-maintenance/prepare-for-npsp-and-salesforce-releases
- [WA-OE] https://architect.salesforce.com/docs/architect/well-architected/guide/operational-excellence.html
- [WA-R] https://architect.salesforce.com/docs/architect/well-architected/guide/reliability.html
- [WA-RC] https://architect.salesforce.com/docs/architect/well-architected/guide/resource-and-cost-optimization.html
- [WA-T] https://architect.salesforce.com/docs/architect/well-architected/guide/trust.html
- [SRE] https://sre.google/sre-book/postmortem-culture/
- **verify** items: API retirement (https://developer.salesforce.com/docs/platform/api-rest/guide/api-rest-eol.html).
- field-reported: lessons from real client work with Torque, including an after-action review
  of 166 rework and defect episodes. Not re-tested for this release.
