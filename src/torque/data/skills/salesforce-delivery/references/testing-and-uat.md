# Testing and UAT

Use this before you call anything ready for review, UAT or production. State described:
Winter '27 (API 68.0), checked 2026-11-14. **verify** = not confirmed in a fetched primary
source. field-reported = from real client work, one consultant, mostly sandboxes: an
after-action review of 166 rework episodes found that 46% were caught by someone other than
the agent's own checks. Section 2 is the checks that would have caught them. `[HUMAN]` = a
person decides or acts.

## 1. Design the tests

1. One case per acceptance criterion: positive, negative, permission and bulk [TH-TEST].
2. Build the matrix from the object's child records and whatever consumes them downstream
   (field-reported: early tests skipped multi-payment and accounting cases).
3. Bulk: 200 records through the real entry point, not a loop of single saves [WA-R].
4. Answer these before you call the build ready. In the field record about three in five
   first-round review findings on a build plan (32 of 51) were prompted by one of them:

| Question | Ask |
| --- | --- |
| Re-run | The same input again (resend, retry, re-file): is what was created found next time? |
| Matches | None, one, several, conflicting: what happens to the values already there? Later edit, delete, merge? |
| Side effects | Is every channel to real people or shared systems shut before the first write? |
| Extremes | Empty, partial, maximum length, contradictory input: validated on the server? |
| Volume and failure | Worst case per transaction, a limit error that cannot be caught, a failure between two writes, two runs at once: how does it recover? |
| Existing automation | Which rules fire on insert and on update, for legacy records too? |
| Assumptions | Was each platform or vendor behaviour probed before the work that depends on it? |
| Proof | Would each check's result differ if the bad thing happened? |
| Who runs it | Exactly the rights needed? Does any rule catch our own automation? |
| After each revision | Is the change applied to every parallel branch, and are tests and recovery steps re-derived? |

5. After a fix, rerun the whole journey and whatever consumes its output next, not the fixed
   step (field-reported: fixes moved records to the wrong household, skipped an approval and
   hid a button).

## 2. Checks that would have caught what escaped

All field-reported. "Escaped" means found by a tester, a reviewer, the lead or the client, or
by the agent only after it had recorded a pass.

| What escaped | The check at build or test time |
| --- | --- |
| A tester could not remove a row they did not own | Give the rows to a second test user; delete as a non-admin |
| Two staff groups in turn could not open a new screen | Sign in as one person per user group, in the UAT org, before hand-over |
| One profile could not see a list that a user-mode code check had "proven" | Sign in as a real user of each profile: a code check is not the page |
| A button showed on a record type it was not meant for | Open one record of every record type that shares the page layout |
| A screen recorded as verified twice had its totals and its add button off-screen | Non-admin, small window, zero starting rows, no scrolling before the assertion; look at the screen |
| A rule covered one record shape; testers used another; a third never produced the signal | One aggregate query of the real shapes in production before writing the rule (one such study took 45 minutes and found 12); steps name which shapes should and should not change |
| A rule's signal was missing on a fifth of the real candidates | One aggregate query of the candidates by the chosen signal |
| A data-quality report missed the org's most common placeholder value | Profile real values by frequency before writing the filter |
| A lock keyed to a stage the client does not use; a filter on a field empty in real data | A negative case at the client's real stage; count blanks per filter field in the UAT org |
| A new before-save step stopped approvals for seven hours | After a change, assert the downstream outcome end to end |
| A step added to a colleague's flow would have rolled back their steps on failure and re-dated exported rows on its first run | Inject a fault into any step you add to someone else's automation; ask what the first run does to existing rows |
| A first full recalculation wrote to every record | Count what a first run will write and what those writes fire; scope it |
| A repeat submission regressed a verified fix to record matching | Snapshot matched records before and after any change to matching |
| A redesign would have overwritten details when it matched an existing person | For every match ask what happens to the values already there |
| A valid redistribution failed inside a managed package after hand-over | Exercise the package's own edge cases (fully allocated, fractional) before hand-over |
| Failed connector steps on 26 of 35 runs were not counted as failures, and notices reached the client | A clean vendor run log is part of "pass"; find where failure notices go before the first test |
| A restore after a refresh was verified by component names while public endpoints had lost access; a reinstalled package re-activated a flow | Rerun behaviour checks after a restore; compare the active-flow list before and after an install |
| A feasibility result was read from two identical files | A probe must be able to tell the outcomes apart: a timestamp or a marker edit |
| A package was not self-contained, twice | Check-only deploy from the manifest alone |
| A tool passed its own tests; reviewers found cleanup that could delete another run's records | Negative list before build: fail a middle step, replay a request, point an option at a record the run did not make, clean up during a run, dry-run every removal |
| A field nobody had required was removed in UAT with four deploys | Trace every new field to a requirement line; close the open question before building |
| A tester could not sign in; defects known before UAT reached it | The hand-over pack (section 5) |

## 3. Test as the real user

Run the journey once per intended persona as a non-admin, on a record someone else owns, in
a small window, from an empty state. Admin tests and user-mode code checks passed while real
users were blocked (field-reported, 13 episodes).

1. Sign the browser in with `torque browser login` (see `torque browser login --help`). It
   uses the CLI's authorization and a single-use address. Do not type a password, and do not
   ask the consultant to sign in where the CLI is authorized.
2. Become the user: Setup "Log in as" (needs Modify All Data and the org's login-access
   policy; not for inactive users; login flows are skipped; **verify**), or a test user that
   mirrors the real user's profile and permission sets.
3. Confirm whose session it is before each test: the "Logged in as" banner. It can render
   late: wait for it [ADV `login-as-servlet-beats-the-setup-link`]. Without it you are
   testing as the admin and everything passes (field-reported).
4. Look at the screen. Record the user, the method, the org and the window size.

The agent cannot do some sign-ins alone: a shared login whose code goes to someone's mailbox,
a passkey, a production org with no CLI authorization. Say so; a person signs in.
An API-only user cannot be opened in a browser (**verify**): use a separate test user. A
phone-only page rule cannot be proven in a desktop browser: state the gap.

## 4. Before the first test record, and after the last

- Find what a test can send outside the org first:
  `salesforce-build/references/integration.md`, section 7. Create one record, inspect jobs
  and queues, then go on (field-reported: test data queued jobs to outside systems, and up
  to 28 failure notices reached a client's mailbox).
- In production, test on dummy records only, never a real person's. Rehearse any move that
  re-parents a record in the sandbox first (field-reported: in production the same move made
  automation create a new account). Put the dummy back as found.
- Keep a leftover ledger from the first record: records, and temporary schedules,
  assignments, users, credentials and rules. Close each line as cleaned, kept on purpose or
  waiting on its owner (field-reported: 12 of 21 leftover sets were never cleaned).
- Save each test record's Id when you create it. Delete by those Ids only, never by a query
  on name or date.
- Developer sandboxes hold no production data: create it. For small related sets,
  `sf data export tree` and `sf data import tree` [PLUGIN-DATA].
- Performance tests run only in a Full sandbox, with Salesforce Support told first
  [TH-RELTEST].

## 5. UAT

UAT is testing by the people who do the job, in a sandbox, "the last stage before go live"
[TH-UAT]. Technical QA comes first and is not UAT.

The hand-over pack (field-reported: 18 of 20 UAT items in one engagement were sign-in,
access, script errors and open scope questions, not logic):

1. Per tester: the exact username for this org, and proof they can sign in.
2. Access proven by signing in as them or as a mirror, not read from a permission set.
3. Rows they own and rows they do not.
4. Steps regenerated from the current build, with preconditions and where each action is
   found. Dry-run the script end to end yourself first.
5. Known open defects and open scope questions, listed.
6. Who answers what.

Running it:

- Entry: the build is deployed to the UAT org, the data is ready, QA passed. Confirm the
  deployed version before each test wave and time-stamp every result: shared test orgs move
  under you (field-reported).
- Triage each item. A defect against built behaviour or an accepted criterion: fix or answer
  it. A scope or design-intent question: facts only, to whoever wrote the specification, no
  commitments. Diagnose a tester's remark before you change anything (field-reported: a
  colleague's logic was changed for a remark that turned out to be test data).
- Severity: 1 critical function, all users, data loss or security; 2 a critical function
  degraded; 3 limited impact [WA-OE].
- Exit: critical scripts pass, no open severity 1 or 2, deferred defects accepted in writing.
- `[HUMAN]` the business owner signs: scope tested, results, open defects accepted, name,
  date. Acceptance is never set by the agent. Sign-off without real coverage is the
  cautionary case in Salesforce's own training [TH-UAT].

## 6. Report results

One table in test-plan order: test, expected (written before you mark a fail), actual,
status, evidence. Reproduce each finding twice, with real key presses, before it goes to the
builder (field-reported: a first pass held false findings from the input method and marked
one correct behaviour as failed). Record the build state at start and end. Say what was not
tested and where each test ran.

## Artifacts

| Artifact | Minimum contents |
| --- | --- |
| Test script | Id, story and criterion, persona, preconditions and data, steps, expected, actual, status, evidence |
| UAT and defect log | Case, tester, date, result, defect id, severity, priority, status, retest |
| UAT sign-off | Scope tested, results, open defects accepted, name, date |
| Leftover ledger | What was created or changed for a test, where, state: cleaned, kept, waiting |

## Sources

- [TH-TEST] https://trailhead.salesforce.com/content/learn/modules/apex_testing/apex_testing_intro
- [TH-UAT] https://trailhead.salesforce.com/content/learn/modules/user-acceptance-testing-video/learn-about-user-acceptance-testing
- [TH-RELTEST] https://trailhead.salesforce.com/content/learn/modules/advanced-salesforce-release-readiness-strategies/test-new-features-before-a-release
- [WA-R] https://architect.salesforce.com/docs/architect/well-architected/guide/reliability.html
- [WA-OE] https://architect.salesforce.com/docs/architect/well-architected/guide/operational-excellence.html
- [PLUGIN-DATA] https://raw.githubusercontent.com/salesforcecli/plugin-data/main/README.md
- [ADV] Torque advisory catalogue (`torque advisory notes --command TEXT`); entry id inline.
- **verify** items: Log in as (https://help.salesforce.com/s/articleView?id=xcloud.logging_in_as_another_user.htm&type=5).
- field-reported: an after-action review of 166 rework and defect episodes and the working
  notes of the same period (one consultant, about a dozen clients, mostly sandboxes). Not
  re-tested for this release.
