# Apex and Lightning web components: the minimum

Use this once code is the chosen tool (`choosing-the-tool.md`, section 8). It is the floor,
not a style guide. State described: Winter '27 (API 68.0), checked 2026-11-14. `[check]` =
testable in the source files. **verify** = not confirmed in a fetched primary source: verify
in the org or the current documentation. field-reported = seen in client work, usually one
org.

Salesforce publishes its own agent skills for generating Apex and components
(`forcedotcom/sf-skills`). Where they are installed, use them for generation and treat this
file as the review list [SK-APEX].

## 1. Before the first line

1. The reason for code is recorded, and the client agreed to own code.
2. You know what already runs on the object: triggers, flows, package handlers
   (`torque advisory impact --target-org ALIAS --sobject Object__c`).
3. You read the API version of every class you will change. Do not raise it in passing
   (section 3).

## 2. Structure and bulk safety

- One trigger per object, with no logic in it: it hands over to a handler class, and one
  service method holds each rule so triggers, flows and jobs share it [SK-APEX] [P-CODE].
  Where a managed package owns the object's trigger, register with the package's framework
  (for NPSP, skill `salesforce-npsp`).
- "Place all SOQL outside loops" and "Place all DML outside loops" [SK-APEX]. Collect Ids,
  query once, map by Id, one DML per object.
- Design for 200 records per transaction: 200 should cost the same number of queries and DML
  statements as one [WA-R].
- Leave headroom. Salesforce's pages give two targets: finish within 70% of a governor limit
  [WA-R], and stay "well under half" [WA-RC]. Record which one the design uses.
- Limits per transaction, synchronous then asynchronous (**verify** with the `Limits` class
  in the org): queries 100 then 200; rows read 50,000; DML statements 150; rows written
  10,000; CPU 10 then 60 seconds. Salesforce pages disagree on the heap limit after
  Winter '27: read it in the org.
- Setup objects (User, permission sets) and ordinary records cannot be written in one
  transaction [WA-RC].
- No hard-coded Ids, record types, users or endpoints: "Use Custom Metadata/Labels/describe
  calls instead of hardcoded IDs", and named credentials for endpoints [SK-APEX].
- "Always handle exceptions (log, rethrow, or recover)" [SK-APEX]. Never an empty catch, never
  a stack trace shown to a user, no passwords or personal data in debug logs [P-SDEV].

`[check]` no query or DML inside a loop (Code Analyzer); no 15 or 18 character Id literal;
more than one trigger on an object.

## 3. Sharing, access mode and the API version

The class's API version now decides its security behaviour [REL-S26]:

| | Class at API 66.0 or lower | Class at API 67.0 (Summer '26) or higher |
| --- | --- | --- |
| No sharing keyword | Old behaviour: without sharing (**verify**) | Runs `with sharing` |
| Queries and DML | Old behaviour: system mode unless stated | User mode by default |
| `WITH SECURITY_ENFORCED` | Compiles | Does not compile: use `WITH USER_MODE` or `WITH SYSTEM_MODE` |

- Raising a class to 67.0 or later is a behaviour change with no compile error: fewer rows
  returned, DML failing for users with less access. Add tests as a restricted user first,
  and make the version change its own reviewed change [REL-S26].
- At any version: "Declare a sharing keyword on every class"; say why in a comment when it is
  `without sharing`, and keep that code small; state the access mode on each query and DML;
  use bind variables in dynamic SOQL [SK-APEX] [P-IAM].
- `with sharing` covers record access only, not object or field access
  [ADV `with-sharing-is-not-fls`].
- New classes carry the current API version, set in the file, never inherited from a tool's
  default [ADV `api-version-is-behaviour`].
- Apex called from a component, above all `without sharing`, is a way around the access
  model: re-check object, field and record access in every such method [WA-T].

`[check]` every class declares `with sharing`, `without sharing` or `inherited sharing`; a
changed `apiVersion` in a `*.cls-meta.xml` is called out; no `WITH SECURITY_ENFORCED` in a
class at 67.0 or higher.

## 4. Tests that prove behaviour

- Coverage is a floor: "at least 75% of Apex code must be covered by tests, and all those
  tests must pass", and "Don't just aim for 75% coverage" [TH-TEST].
- Each behaviour gets a positive case, a negative case, a bulk case and a single-record case.
  Bulk means the trigger's real batch of 200.
- Tests create their own data (`@TestSetup` or a factory), never `SeeAllData=true`; they
  mock callouts; they wrap the unit in `Test.startTest()` and `Test.stopTest()` (**verify**).
- At least one test runs as a user with only the intended permission sets
  (`System.runAs`), and one as a user without them to prove the refusal.
- Every test asserts an outcome with the `Assert` class. A test that asserts nothing, a
  skipped test and a commented-out test are named anti-patterns [P-DEVOPS].
- Review generated tests for meaningful assertions before you count them. A generated draft
  from `torque probes` fails at its draft assertion until you replace it, on purpose.

`[check]` no test class without an `Assert.` or `System.assert` call; no `SeeAllData=true`.

Field-tested ways to prove behaviour without leaving data (field-reported):

- Anonymous Apex that does the work and then rolls back shows behaviour and limit usage. A
  savepoint and rollback cost DML statements of their own [ADV
  `savepoint-rollback-does-not-refund-limits`].
- A check-only deployment that runs a test in user mode as real staff proves logic and access
  together; a guarded `Assert.fail('REPORT ...')` returns counts from such a run without a
  debug log.
- `System.runAs` needs an active user. For the negative case use `WITH USER_MODE` as a
  minimum-access user.
- Anonymous Apex traps (error text hidden by a filter, a rollback that rolls back nothing,
  query limits in package-heavy orgs, reserved words): `gotchas.md`, items 44 to 47.
- A check in user mode is not the page: one profile could not see a list that such a check
  had "proven". Finish with a sign-in as a real user of each profile.

## 5. Asynchronous work

Sources: [DG-ASYNC] [F-ASYNC] [DG-RT].

| Need | First choice | Avoid |
| --- | --- | --- |
| Background work, callouts, chaining | Queueable Apex with a finalizer | `@future` ("Never use `@future` methods" [SK-APEX]) |
| Very large record sets, nightly recalculation | Batch Apex, scope 200 to 2,000 | Scope 1; starting it from a trigger |
| Fixed schedule | A scheduled flow for simple cases; Scheduled Apex that starts a batch | Many separate scheduled jobs |
| Time relative to a record | A flow scheduled path | A polling job |
| Tell another system | Platform event or Change Data Capture | An async callout per record |
| The user needs the answer now | Stay synchronous | Any async tool: "Asynchronous patterns have no SLA" |

Never start Batch or Scheduled Apex from a trigger, and treat a Queueable started from a
trigger as a risk [DG-RT]. Work that can run twice (a retried job, a replayed event) must
give the same result twice.

## 6. Apex that flows call

An invocable method takes a list and returns a list, and its `label` and `description` say
what it does: that description is where an admin, or an agent, learns the logic [DG-RT].

## 7. Lightning web components

- Data access, in order of preference: base components built on Lightning Data Service, then
  its wire adapters, then the GraphQL wire adapter, then Apex last (**verify**). The first
  three apply object, field and record access for you; Apex does not.
- After an Apex write, refresh what the page shows (**verify** the current API for this).
- Handle the `error` of every `@wire`, not only `data` [P-AUTO].
- New work is Lightning web components, not Aura [DG-FORMS].
- Prefer base components: they carry labels and keyboard support. Custom markup needs
  semantic HTML, visible focus and tested contrast; add accessibility tests to the
  component's unit tests [WA-F].

## 8. Document, analyze, deploy

1. A `/** */` block on the class and on each public or global method: what it does, why it
   is code, what calls it [SK-APEX].
2. Run Salesforce Code Analyzer on the changed files and clear the high severities:
   "Remediate all sev0, sev1, and sev2 violations; re-run until clean" [SK-APEX]. Commands
   and reading results: skill `salesforce-code-analyzer-review`.
3. Run the org's tests for the change and keep the result with the change record.
4. A production deployment that contains Apex runs local tests by default and can take far
   longer than the sandbox validation; validate ahead of the window and quick-deploy the
   validated job [ADV `prod-deploy-runs-tests`].
5. In a sandbox with a managed package you see its objects and flows but not its Apex, and
   the installed version can differ from any source you studied (field-reported).

Stop and ask before: `without sharing`, `global` or a REST resource; a trigger on an object
that already has triggers or package triggers; scheduling, aborting or re-running jobs in
production; raising API versions; deleting code or tests.

## Sources

- [SK-APEX] https://github.com/forcedotcom/sf-skills/blob/main/skills/platform-apex-generate/SKILL.md
- [REL-S26] Summer '26 architect release highlights: https://www.salesforce.com/blog/summer-26-release-architect-highlights/
- [WA-R] https://architect.salesforce.com/docs/architect/well-architected/guide/reliability.html
- [WA-RC] https://architect.salesforce.com/docs/architect/well-architected/guide/resource-and-cost-optimization.html
- [WA-T] https://architect.salesforce.com/docs/architect/well-architected/guide/trust.html
- [WA-F] https://architect.salesforce.com/docs/architect/well-architected/guide/fairness.html
- [P-CODE] https://architect.salesforce.com/docs/architect/well-architected/guide/resource-and-cost-optimization-code-quality-patterns
- [P-IAM] https://architect.salesforce.com/docs/architect/well-architected/guide/trust-identity-access-patterns
- [P-SDEV] https://architect.salesforce.com/docs/architect/well-architected/guide/trust-secure-development-patterns
- [P-DEVOPS] https://architect.salesforce.com/docs/architect/well-architected/guide/operational-excellence-devops-patterns
- [P-AUTO] https://architect.salesforce.com/docs/architect/well-architected/guide/operational-excellence-automation-patterns
- [DG-RT] https://architect.salesforce.com/docs/architect/decision-guides/guide/record-triggered
- [DG-ASYNC] https://architect.salesforce.com/docs/architect/decision-guides/guide/async-processing
- [DG-FORMS] https://architect.salesforce.com/docs/architect/decision-guides/guide/build-forms
- [F-ASYNC] https://architect.salesforce.com/docs/architect/fundamentals/guide/async-fundamentals
- [TH-TEST] https://trailhead.salesforce.com/content/learn/modules/apex_testing/apex_testing_intro
- [ADV] Torque advisory catalogue (`torque advisory notes --command TEXT`); entry ids inline.
- **verify** items: governor limits (https://developer.salesforce.com/docs/atlas.en-us.apexcode.meta/apexcode/apex_gov_limits.htm),
  Apex testing practice (https://developer.salesforce.com/docs/platform/apex-dev/references/apex-dev/apex-testing-best-practices.html),
  component data guidelines (https://developer.salesforce.com/docs/platform/lwc/guide/data-guidelines.html).
- field-reported: lessons from real client work with Torque and its predecessor (one
  consultant, about a dozen clients, mostly sandboxes): working notes, an after-action review
  of 166 rework and defect episodes, and a classification of 273 failed Salesforce CLI calls.
  Not re-tested for this release.
