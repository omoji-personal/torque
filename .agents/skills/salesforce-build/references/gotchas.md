# Build gotchas, each with a check

Platform and tooling traps met while building configuration. Flow traps are in
`salesforce-flow/references/gotchas.md`. State described: Winter '27 (API 68.0), checked
2026-11-14. Evidence is named on each item: [ADV] (Torque advisory catalogue, with its own
confidence label), field-reported (real client work, usually one org) or **verify** (not
confirmed in a fetched primary source). None was re-tested for this release: run the check.

## Fields and access

1. **A new field deployed without field permissions is invisible.** The deploy succeeds and
   the field is missing from pages, list views, reports and queries [ADV `fls-not-automatic`,
   verified live]. The most frequent build miss in the field record. Check:
   `SELECT Field, Parent.Name FROM FieldPermissions WHERE Field = 'Object__c.Field__c'`.
2. **Three kinds of field break the usual grant.** A formula field's entry needs `editable`
   false or the whole deploy fails; required and master-detail fields take no entry at all
   [ADV] [SK-PS]. Check: a dry-run deploy of the permission set.
3. **New fields from a managed-package upgrade can be hidden from everyone,** administrators
   included, until access is granted (NPSP releases [TH-NPSP]). Check: read the package's
   release notes for new fields after each upgrade and query `FieldPermissions`.
4. **A formula field can show data its reader cannot see,** because access is checked on the
   formula, not its inputs [ADV `formula-fields-bypass-fls`]. Check: list formulas that
   reference Confidential or Restricted fields and compare who can read each.
5. **A deleted field is renamed with `_del` and held for 15 days,** still counting against
   the object's field limit [ADV `deleted-field-rename`, verified live]. Check, Tooling:
   `SELECT DeveloperName FROM CustomField WHERE TableEnumOrId = 'Account' AND DeveloperName LIKE '%_del'`.
6. **Required on the page layout is not required to the API,** and required on the field
   binds every write [ADV `layout-required-is-not-api-required`, verified live]. Check:
   `nillable` in describe is the API's rule.

## Rules and picklists

7. **A validation rule that tests a text field with `ISNULL` never fires** (field-reported).
   Check: save a record with the field empty; if the rule stays silent, change the test for
   blank.
8. **A restricted picklist value must also be enabled on the record type,** or integration
   writes fail (field-reported). Check: write one record through the integration user for
   each record type.
9. **A picklist default is applied on API inserts too** (field-reported). Check: insert
   through the API without the field and read it back.
10. **A restricted picklist rejects an inactive value, even one the record already holds**
    [ADV `restricted-picklist-rejects-inactive`]. Check: compare active values with the
    values in the data before a mass update.
11. **Names in a spec are not names in the org.** A rule named a stage that did not exist, a
    spec was one word off the real API name, and two sandboxes named the same field
    differently (field-reported). Check: describe output for every name before you design.
12. **A rule built from a few examples misclassifies real data** (field-reported: about a
    hundred misses and three false hits). Check: run the condition read-only over all
    existing records and explain every surprise before you deploy.

## Pages

13. **Bulk edits of Lightning page files remove more than you meant** (field-reported: every
    section header on a live page, during user testing). Check: after deploy, retrieve and
    count section labels against the baseline.
14. **A field on the layout does not show on a page that uses Dynamic Forms,** and the
    reverse (**verify** on the page in question). Check: `pages-and-reports.md`, section 1.
15. **Lightning caches rendered pages** (field-reported). Check: reload fully or start a new
    session after a deploy before you call a page fix failed.
16. **A removed quick action stays on a layout** unless an empty `platformActionList` is
    deployed first (field-reported). Check: retrieve the layout after the removal.
17. **Phone-only page rules cannot be tested in a desktop browser** (field-reported). Check:
    state the gap; verify field access and the page's phone assignment.

## Deploy and retrieve

18. **Exit code 0 is not proof.** An invalid CLI flag printed help and exited 0, so nothing
    happened and the next step ran on nothing (field-reported). Check: read the org's state
    back after every write.
19. **Retrieve traps** (field-reported): `--output-dir` must be inside the project (use
    `--target-metadata-dir` for a scratch location); a manifest wildcard returns only
    unmanaged custom objects; a wildcard Apex retrieve skips unlocked-package code; a
    package's layouts need the namespace inside the layout name. Check: compare the number
    of files retrieved with `sf org list metadata --metadata-type TYPE --target-org ALIAS`.
20. **Deploy order matters across separate deploys,** not inside one: in one payload the API
    resolves dependencies [ADV `deploy-order`]. Check: prefer one deploy; if you split, a
    component goes after what it references.
21. **A profile deploy overwrites what the file says, including explicit `false` values,**
    and leaves the rest alone [ADV `full-profile-deploy`, verified live]. Check: read a
    profile file for `false` entries before it goes anywhere.
22. **`--purge-on-delete` belongs to a destructive deploy,** not to
    `sf project delete source` [ADV `purge-flag-belongs-to-deploy`], and works only in
    sandboxes and Developer Edition orgs, not production (**verify**, Metadata API
    `purgeOnDelete`). Leave it out unless asked.
23. **Small deploy dependencies** (field-reported): a test custom metadata record had to be
    deleted before the real mapping record deployed; a rollup tool's rollup on a new object
    needed its child trigger and test deployed too; a platform-event subscriber change needed
    a manual suspend and resume. Check: a dry run in a sandbox that matches production.
24. **Secrets hide in retrieved metadata.** Custom metadata, custom settings and "sample
    request" fields can hold live-looking credentials (field-reported). Check: scan the
    retrieved copy for secret-looking pairs before you open files; print key names and hosts
    only.

## Managed packages

25. **Managed components are locked.** "Cannot modify managed object" came from changing a
    field set's description [ADV `managed-fields-immutable`]. Check:
    `[check: managed.component-in-change]`.
26. **Never deploy your own copies of a package's custom metadata records.** A copy without
    the namespace created a duplicate developer name that made the package's trigger fail on
    every insert (observed in a developer org; skill `salesforce-npsp`). Check: query the
    type for duplicate `DeveloperName` values.
27. **The namespace prefix shows in the packaging org too** [ADV
    `namespace-in-packaging-org`]. Check: resolve names against the org the code runs in.
28. **A beta package version cannot be installed in production or upgraded in place,** and
    `sf package installed list` does not show the flag (field-reported). Check, Tooling:
    `SubscriberPackageVersion.IsBeta` on `InstalledSubscriberPackage`.
29. **A package hides its Apex in a subscriber sandbox,** the installed version can differ
    from the source you studied, and one packaged test failed in the first half of every
    year and blocked uploads (field-reported). Check: the installed version; date-dependent
    tests before a release date.
30. **Opening a package's settings page can write.** Opening NPSP Settings in a sandbox
    inserted default trigger handlers and scheduled jobs (field-reported). Check: in a
    read-only study, do not open it; compare handler and job counts before and after.

## Orgs and sandboxes

31. **After a sandbox refresh every CLI call can fail** with "The requested resource does
    not exist", because the CLI caches the org's highest API version for a day
    (field-reported). Check: pass the API version explicitly; an Organization query succeeds.
32. **What a refresh brings and loses** (field-reported): custom settings return with
    production values; scheduled jobs, sandbox-only permission set assignments and registered
    sign-in methods do not; production passwords are copied. The org Id changes too [ADV
    `sandbox-refresh-changes-orgid`]. Check: endpoints, `CronTrigger`, assignments and any
    user name stored in automation.
33. **"Not a sandbox" is not "production".** A Developer Edition org reports `IsSandbox`
    false [ADV `developer-edition-is-not-sandbox`, verified live], and Full and Partial Copy
    sandboxes hold real production data [ADV `sandbox-contains-real-data`]. Check:
    `SELECT Id, Name, IsSandbox, OrganizationType FROM Organization`.
34. **Logging out of the CLI does not revoke the token** (field-reported). Check: revoke the
    session in the org, then log out; a query through the alias no longer authenticates.
35. **The platform changes three times a year.** A remembered attribute or API name is the
    most common cause of a failed deploy [ADV `three-releases-a-year`]. Check: describe
    before you reference.

## Working without API access

36. **The Developer Console Query Editor is a read-only route** when a production org has no
    CLI authorization: reviewed SELECT queries that return counts or configuration names, the
    consultant signs in, no Execute Anonymous, and say first that opening the console turns
    on debug logging for that user (field-reported). Check: use `COUNT(field)` per column;
    reading result grids through page tools dropped short cells.

## Command-line failures from the field record

Field-reported, from 273 failed Salesforce CLI calls in one consultant's work. The error
text was observed; each fix is the one that worked.

37. **Check-only before every real deploy.** Check-only runs caught 28 of 36 deploy
    failures; 7 of the 8 real deploys that failed had no check-only run just before. A
    package that was "ready" twice lacked a field and a permission set. Check: a dry run
    from the manifest alone passes.
38. **A pipe hides a failure.** 44% of failed CLI calls looked successful because a filter,
    a redirect or a chained command swallowed the exit code. Check: write `--json` output to
    a file and read the file; do not merge the error stream into it.
39. **Deploy and retrieve need a project folder, or the metadata-format flags.** Outside a
    project: `--target-metadata-dir DIR --unzip` to retrieve, `--metadata-dir DIR` to
    deploy. Check: `sfdx-project.json` and its package directory exist before the command.
40. **`sf project deploy validate` offers no `NoTestRun`:** it requires Apex tests, and the
    CLI says `NoTestRun` applies only to development environments [CLI-DR]. In the field
    record three validate attempts failed on it, and one production deploy with "testLevel of
    NoTestRun cannot be used in production organizations". Without
    `--test-level` the default depends on the payload and the org [CLI-DR]. Check: in a
    sandbox use `deploy start --dry-run`; for production, name the test level you mean.
41. **A connected app would not deploy** ("You can't create a connected app"). Check whether
    the org still allows new connected apps; an external client app is the replacement.
42. **A broad retrieve cannot always be redeployed.** It held members the platform created
    that fail on deploy. Check: prune to what you changed.
43. **A script file written by Windows PowerShell can start with a byte-order mark,** and
    anonymous Apex then fails with "Invalid identifier at Line 1 column 1". Check: write
    files with the editor tool; look at the first bytes when line 1 column 1 is blamed.
44. **A narrow filter on `sf apex run` output hides the reason.** Check: use `--json` and
    read the compile problem or the exception message with its line and column.
45. **`Database.rollback(Database.setSavepoint())` at the end of a script rolls back
    nothing.** Check: set the savepoint first, do the work, roll back to it.
46. **Several test cases in one anonymous script ran out of queries in a package-heavy
    org** (each save fired many package queries). Check: one to three cases per transaction;
    a rollback does not refund limits [ADV `savepoint-rollback-does-not-refund-limits`].
47. **Reserved words fail as Apex variable names,** sometimes with a misleading "Unexpected
    token" on the next word (seen with `any` and `bulk`). Check: rename the variable first.

## Sources

- [ADV] Torque advisory catalogue (`torque advisory notes --command TEXT`). Each entry carries
  its own label: verified live, documented or practitioner.
- [SK-PS] https://github.com/forcedotcom/sf-skills/blob/main/skills/platform-permission-set-generate/SKILL.md
- [CLI-DR] Salesforce CLI deploy plugin 4.1.2 help (`sf project deploy start --help`,
  `sf project deploy validate --help`), read 2026-11-14; the same text is in
  https://github.com/salesforcecli/plugin-deploy-retrieve/blob/main/README.md
- **verify** items: purgeOnDelete (https://developer.salesforce.com/docs/platform/api-metadata/references/file-based-calls/meta-deploy-deleting-files.html),
  read as a search extract.
- [TH-NPSP] https://trailhead.salesforce.com/content/learn/modules/nonprofit-success-pack-maintenance/prepare-for-npsp-and-salesforce-releases
- Observed in a developer org: item 26, carried from the earlier nonprofit skill text.
- field-reported: lessons from real client work with Torque and its predecessor (one
  consultant, about a dozen clients, mostly sandboxes), including a classification of 273
  failed Salesforce CLI calls and an after-action review of 166 rework and defect episodes.
  Not re-tested for this release.
