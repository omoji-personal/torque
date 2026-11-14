# Flow gotchas, each with a check

Read the section that matches what you are doing. State described: Winter '27 (API 68.0),
checked 2026-11-14. Evidence is named on each item: "observed in a developer org" (carried
from the earlier nonprofit skill), field-reported (real client work, usually one org),
[ADV] (Torque advisory catalogue) or **verify** (not confirmed in a fetched primary source).
None was re-tested for this release, so run the check before you rely on an item. A tag
such as `[check: name.case-collision]` names the `torque check metadata` rule that tests it.

## Faults and transactions

1. **A handled fault on a collection Create can partially save.** With a fault connector,
   the rows that succeeded stay saved and the fault path runs; Ids are not written back to
   the collection. Only an unhandled fault rolls back the whole transaction. For
   all-or-nothing, put a Roll Back Records element on the fault path before any logging (or
   the log is rolled back too) where the flow type offers it, or move the work to Apex.
   Observed in a developer org. Check: create three records where one fails a validation
   rule, then count what was saved.
2. **A fault path does not catch a governor-limit failure.** The transaction still rolls
   back (**verify**). Check: run 200 records and read the limit usage in a debug log.
3. **A Subflow element can fault, and may not offer a fault connector.** Code Analyzer's
   `MissingFaultHandler` counts subflows among the elements that need a fault handler
   [CA-LOCAL]; an earlier developer-org test found no fault connector on a Subflow element.
   Check: whether the Subflow element in Flow Builder offers Add Fault Path in this org. If
   it does, add one; if not, handle faults inside the subflow and return an error output
   the parent checks.

## Formulas and values

4. **Flow formula `DATEVALUE()` on a date/time uses GMT.** An evening value in a US time
   zone dates to the next day, and a late December 31 value lands in the next year. Use a
   date formula field on the object, which evaluates in the user's time zone, or adjust
   explicitly. Observed in a developer org. Check: evaluate on a record stamped 11 PM local.
5. **A formula sees a choice field's label, not its value.** In one org, a formula
   `{!field} = 'VALUE'` on a choice screen field never matched where the label differed
   from the value, and the flow took its default path (field-reported: it created stray
   records). Where label and value differ, compare the field to the choice resource in a
   decision or visibility rule.
   `[check: flow.choice-compared-to-literal]`. Check: run the screen once per choice and confirm
   the non-default branch runs.
6. **Flow Builder debug runs commit unless rollback mode is on.** A debug run of an
   autolaunched or screen flow writes real records when the rollback option is cleared
   (observed in a developer org). Record-triggered debug runs in rollback mode (**verify**).
   Check: after any debug run, query for records it could have created and delete them by Id.
7. **Three formula and filter traps** (field-reported): `CONTAINS(text, "")` is true; `"\n"`
   is not a new line in a formula; a null inside a set of Ids used in an IN filter returns
   rows you did not intend. Check: test the formula with an empty value; count the rows the
   filter returns with a null in the set.

## Screen flows

8. **A screen action without its trigger deploys and never runs.** `<actions>` without
   `<triggers>` shows as configured; the deploy also accepts a made-up `eventName`
   (field-reported). `[check: flow.screen-action-without-trigger]`. Check: the
   action runs on screen load in a browser test. To get a working shape, retrieve a flow
   where the action was set up in Flow Builder.
9. **A screen flow on a record page can need more than profile flow access.** In one org,
   with profile flow access alone the user got "Insufficient Privileges", the page
   component was silently hidden, and Run Flows granted by permission set fixed it
   (field-reported). Read how the flow's access is set (item 10) and grant the narrowest
   access that works, by permission set. Check: signed in as that user, the component
   renders.
10. **The flow access override persists.** Omitting `isAdditionalPermissionRequiredToRun` in
    a new version kept the previous `true` (field-reported). Decide the value on purpose:
    where the flow is restricted to some users on purpose, keep `true` and grant the flow
    in their permission set; send an explicit `false` only when the restriction should go.
    By hand: the element is present with the intended value. Check: read the flow's access
    setting in Setup after the deploy, and run it as a user who should not have it.
11. **Data Table behaviour** (field-reported): radio buttons appear only with maximum
    selection 1 and minimum selection at least 1; ticks are lost when the source collection
    changes; a default selection is not re-applied when the next search returns the same
    rows. Check: search twice in a browser and read the selection each time.
12. **A screen checkbox without `isRequired` in the file did not activate;** with
    `<isRequired>true</isRequired>` it did (field-reported, one flow). Whether that value
    also forces the user to tick the box was not recorded. Check: retrieve a checkbox
    Flow Builder saved in this org and copy its value, then activate in a sandbox and
    submit the screen once without ticking it.

## Deploy and metadata

13. **Resource names are case-insensitive.** Two names that differ only in case fail the
    deploy with "Duplicate developer name" (field-reported). `[check: name.case-collision]`.
14. **Get Records with `storeOutputAutomatically` also needs `getFirstRecordOnly`**
    (field-reported). Set `true` when the flow uses one record, `false` when it uses the
    collection: `true` keeps only the first row. `[check: flow.lookup-missing-first-record-only]`.
15. **Decision outcome names must be unique** (field-reported; one deploy reported it as
    "duplicate value found: FlowRecordVersionId"). `[check: name.case-collision]`.
16. **A retrieve returns the latest version, not the active one** [ADV
    `flow-retrieve-latest-only`]. Check: `torque advisory flow`, or `ActiveVersionId` against
    `LatestVersionId` on `FlowDefinition`.
17. **`FlowDefinitionView` is a standard object.** Queried with the Tooling flag it errors,
    and some clients show zero rows [ADV `flowdefinitionview-is-standard-api`]. Check: query
    it without `--use-tooling-api`.
18. **Retrieves of the same flow differ in element positions** (field-reported: zeroed
    locations). Check: a diff that is only positions is not a change.
19. **A script that regenerates a flow drops its descriptions** unless they are kept as data
    and re-applied (field-reported). Check: `[check: doc.flow-elements-undescribed]` after
    each build.

## Run order and side effects

20. **A new flow changes the run order on the object.** A new before-save flow made
    submissions skip approval (field-reported). Check: trigger order set; one existing path
    rerun after the deploy.
21. **Saving a child can save its parent again.** Saving an Opportunity Product re-saves its
    Opportunity, so Opportunity flows run once per line item (field-reported: 100
    Opportunity saves took 7 to 13 seconds of CPU in one org). Check: measure CPU in a debug
    log first; run one-time updates in batches of 50 or fewer.
22. **A save that changes nothing still fires flows** whose conditions do not test for a
    change, and stamps the record as modified [ADV `no-op-update-is-not-free`]. Check: diff
    before a mass update and write only the records that change.
23. **"Only when a record is updated to meet the condition" does not fire again** while the
    record stays in the condition [ADV `entry-condition-requirements`]. Check: save a record
    that already meets it and read the result.
24. **A duplicate rule can block the save after a before-save flow has run**
    [ADV `duplicate-rules-block-the-save`]. Check:
    `SELECT DeveloperName, IsActive, SobjectType FROM DuplicateRule WHERE IsActive = true`.
25. **Email, asynchronous Apex and asynchronous paths run after commit.** A query straight
    after the save shows none of their effects [ADV
    `post-commit-work-is-invisible-to-the-caller`]. Check: poll the record they write to, or
    `AsyncApexJob`.
26. **Test records fire everything that fires on create.** A Process Builder process sent
    marketing events for test contacts, and integrations in a sandbox fell back to production
    endpoints (both field-reported). Check: the inventory in `build-checklist.md`, step 1,
    before the first test record; see `salesforce-delivery/references/testing-and-uat.md`.

## Scheduled and background work

27. **Schedule start times end in Z but are org local time** (field-reported). Check: read
    the next run time in Setup after the deploy.
28. **Before you say when or whether automation runs, read it.** Active version, schedule,
    weekday or entry conditions, and run-as user; for a rollup tool, its mode and any
    scheduled full calculation. field-reported: a daily flow acted only on some weekdays, and
    a "real time" rollup also had a weekly scheduled run; the wrong statement reached a
    client page. Check: the `CronTrigger` rows and the last `AsyncApexJob` for that job.
29. **Flow Orchestration background steps** (field-reported): steps run as Automated Process
    failed on an industry object; `$User.Username` resolved to that user; a run-as setting
    that needs a user name failed when given a user Id; and a production user name set there
    survived a sandbox refresh and failed in the sandbox. Check: run background steps as a
    named licensed user; reset per-org user names after every refresh.
30. **A schedule moved for a test stays moved** (field-reported: for days). Check: no open
    restore at the end of the session; verify by the real scheduled run and a count of
    records created since the fix.

## Diagnosis

31. **"The release broke it" needs evidence.** Compare failing and succeeding runs by owner,
    run-as user and flow version before you accept it; rule out a suspected cause with a
    number (field-reported: the evidence pointed to configuration). Leave unrelated defects
    you find alone and recommend their cleanup.

## Deploy failures and escaped defects from the field record

Field-reported: the error text was observed; the cause is the reading of whoever fixed it.
Each fix is the one that worked.

32. **A flow formula cannot read a long text area field,** and only some functions accept a
    multi-select picklist: the deploy fails with "The formula expression is invalid". Fix:
    copy the value into a text field or variable first, or drop the reference. By hand: the
    field types a formula reads, from describe.
33. **"Property X not valid in version N."** A flow retrieved at a newer API version than the
    project declares did not deploy. Fix it for that operation (pass `--api-version` on the
    retrieve or deploy), not by lowering `sourceApiVersion` in `sfdx-project.json` for one
    file: it is the project-wide version for every deploy and retrieve, so newer elements in
    other files can then fail. Change it only as its own tested change.
    `[check: flow.api-version-behind]`; by hand, `git diff sfdx-project.json` before a deploy.
34. **"Element X is duplicated at this location in type Flow."** Elements with the same tag
    must sit together in the file. Fix: move a hand-added element next to its siblings of
    the same tag. By hand: same-tag elements are contiguous.
35. **A constant cannot be a DateTime** ("The data type of X can't be DateTime"). Fix: use a
    formula in its place. By hand: constant data types.
36. **A field that any flow version reads cannot be deleted.** The destructive change fails
    with "This custom field is referenced elsewhere: Flow Version", for inactive versions
    too. Fix: remove the reference and delete the old versions first. Check: search every
    version before a field delete.
37. **Installing or reinstalling a package can re-activate a flow** (seen after a sandbox
    restore). Check: compare the list of active flows before and after any install.
38. **A step added to someone else's flow shares its transaction.** A failure in an added
    step would have rolled back the owner's own steps, and its first run would have re-dated
    rows already exported (caught in review). Check: inject a fault into the new step and
    read the original steps' results; ask what the first run does to existing rows.
39. **A first run writes more than later runs.** A first full recalculation wrote to every
    record and was noticed three days later. Check: count the rows a first run will write
    and what those writes fire; limit the first run.

Flows on NPSP and Nonprofit Cloud objects have their own items: skills `salesforce-npsp` and
`salesforce-nonprofit-cloud`.

## Sources

- [ADV] Torque advisory catalogue (`torque advisory notes --command TEXT`). Entries
  `flow-retrieve-latest-only`, `flowdefinitionview-is-standard-api` and
  `duplicate-rules-block-the-save` are marked verified live there;
  `post-commit-work-is-invisible-to-the-caller` documented; `no-op-update-is-not-free` and
  `entry-condition-requirements` practitioner.
- Observed in a developer org: items 1, 3, 4 and 6, carried from the earlier nonprofit skill
  text.
- [CA-LOCAL] Code Analyzer flow engine 0.40.0 rule catalogue as installed (plugin 5.15.0),
  read 2026-11-14: `sf code-analyzer rules --rule-selector flow --view detail`.
- **verify** items: flow fault paths (https://help.salesforce.com/s/articleView?id=platform.flow_build_logic_fault.htm&type=5),
  flow debugging (https://help.salesforce.com/s/articleView?id=flow_considerations_troubleshooting.htm&type=5).
- Record-triggered automation decision guide (transaction control in Flow): https://architect.salesforce.com/docs/architect/decision-guides/guide/record-triggered
- field-reported: lessons from real client work with Torque (one consultant, about a dozen
  clients, mostly sandboxes), including a classification of 273 failed Salesforce CLI calls
  and an after-action review of 166 rework and defect episodes. Not re-tested for this
  release.
