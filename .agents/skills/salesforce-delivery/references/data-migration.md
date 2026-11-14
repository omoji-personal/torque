# Data migration and bulk changes

Use this for a migration, an import, a backfill, a mass update or a mass delete. State
described: Winter '27 (API 68.0), checked 2026-11-14. **verify** = not confirmed in a fetched
primary source. field-reported = seen in client work, usually one org. `[HUMAN]` = a person
decides or acts.

"About 99% of the time you spend on an import will be making sure your data is ready outside
Salesforce" [TH-PREP]. Budget for that.

## 1. Checklist

1. Inventory sources, owners and volumes. Profile completeness, formats and duplicates inside
   the agreed data boundary. Keep an unedited copy of the source.
2. Write the mapping: source field, target object and field, transform, default, owner
   mapping, external Id, load order.
3. `[HUMAN]` the data owner agrees the cleansing and merge rules.
4. Ready the destination (section 2).
5. List the automation to bypass, how, and how each is restored (section 4).
6. Trial 1: a handful of records in a sandbox; inspect them by hand. "Salesforce recommends
   you import using a small test file first" [TH-IMPORT].
7. Trial 2: full volume in a sandbox; time it; fix; repeat until clean.
8. Reconcile (section 6).
9. Write the cutover runbook: freeze or delta plan, backup, order, batch sizes, bypass on,
   load, bypass off, recalculations, reconciliation, the criteria for rolling back.
10. `[HUMAN]` the data owner approves the mapping, the rehearsal result and the production
    load.
11. Production: a pilot batch first, then the full load. Keep the result files with record
    Ids.
12. Restore the automation and prove it is restored. Run the recalculation jobs. Reconcile.
13. `[HUMAN]` the client signs the reconciliation.
14. Delete working copies as agreed. Leave no personal data in sandboxes, spreadsheets or the
    session.

## 2. Ready the destination

Read these from describe before the load, not from the result after it:

| Check | Why | Evidence |
| --- | --- | --- |
| Every non-nillable createable field is in the payload | Required on a layout is not the API's rule; `nillable` false is | [ADV `layout-required-is-not-api-required`] |
| Picklist values are active, and restricted or not | A restricted picklist rejects unknown and inactive values; an unrestricted one silently accepts anything | [ADV `restricted-picklist-rejects-new-values`] |
| Created date and creator can be set | They are ignored without the org setting "Set Audit Fields upon Record Creation" and its permission; the load still reports success | [ADV `audit-fields-not-writable`] |
| Active duplicate rules on the object | A blocking rule stops rows after automation has run; standard rules are active on a new org | [ADV `duplicate-rules-block-the-save`] |
| Owners exist and are active; parents load before children | "You won't be able to load child records if the parents don't already exist" | [TH-LOAD] |
| An external Id on each loaded object | Loads can be re-run as upserts without duplicates | [PLUGIN-DATA] |
| Dates are in the format the tool reads: the Data Import Wizard follows the locale [TH-IMPORT]; the Bulk API, which `sf data ... bulk` uses, reads `yyyy-MM-dd` for a date and `yyyy-MM-ddTHH:mm:ss.SSSZ` (or with an offset) for a date and time (**verify**) [BULK-DATE]. Unmapped fields are not imported | A locale date in a bulk file fails or swaps day and month. Check: read five Date and Date/Time values in the file before the load | [TH-IMPORT] [BULK-DATE] |

## 3. Tools and commands

| Volume | Tool | Evidence |
| --- | --- | --- |
| Up to 50,000 records, simple objects | Data Import Wizard | [TH-IMPORT] |
| 50,000 to millions | Data Loader or the Bulk API | [TH-IMPORT] |
| Hundreds of thousands and up | Bulk API | [TH-SCALE] |
| Gifts with donors, households and payments in an NPSP org | NPSP Data Importer: it creates the related records in one pass. Run its dry run first; it does not catch every format error | [TH-NPSP] |
| Nonprofit Cloud objects | Not the Data Import Wizard | skill `salesforce-nonprofit-cloud` |

- Upsert on the external Id: `sf data upsert bulk --sobject Object__c --file rows.csv
  --external-id Legacy_Id__c --target-org ALIAS`. Read per-row outcomes with
  `sf data bulk results --job-id JOB_ID --target-org ALIAS` (check each with `--help`)
  [PLUGIN-DATA].
- A bulk job can end as complete with thousands of failed rows. The exit code and the job
  state say the job finished, not that the data changed [ADV `bulk-partial-failure`].
- Sort child rows by parent Id. Bulk API 2.0 runs batches in parallel and has no serial
  option; a lock error is usually about a shared parent [TH-SCALE] [PLUGIN-DATA]
  [ADV `unable-to-lock-row`]. Do not run batch jobs alongside a load.
- If the CSV was written on another system, declare its line ending (`--line-ending`): one
  load failed on "LineEnding is invalid" (field-reported).
- `sf data delete bulk` sends records to the Recycle Bin, where they can be restored for 15
  days, unless `--hard-delete` is passed, which needs its own permission and cannot be
  undone [PLUGIN-DATA] [ADV `recycle-bin-retention`].
- A save that changes nothing is still a write: it fires automation and restamps the record.
  Diff first and send only rows that change [ADV `no-op-update-is-not-free`].
- For a bulk ownership or sharing change, Salesforce Customer Support can enable deferred
  sharing calculation, so sharing is calculated once afterwards [F-SHARE]. Ask early.

## 4. Automation during the load

- Decide per automation: leave on, bypass, or switch off. Record the original value of every
  setting you change and the step that restores it [TH-LOAD].
- Prefer a bypass that is designed in: a custom permission the loading user holds, tested in
  the flow's entry conditions and the validation rule [DG-RT]. Do not disable all validation.
- In an NPSP org a bypassed load skips households, contact roles, payments, allocations and
  rollups: run the matching package batch afterwards (skill `salesforce-npsp`). After an NPSP
  load recalculate rollups from NPSP Settings > Bulk Data Processes [TH-ROLLUP].
- If automation stays on, find what it sends outside the org first
  (`salesforce-build/references/integration.md`, section 7).
- Measure before a mass update where each save is heavy: in one org 100 saves took 7 to 13
  seconds of CPU, and the update ran in batches of 50 or fewer (field-reported).
- A first run of a new rollup or recalculation writes to every record it touches: count
  them, limit the first run to records that need it, and check the next day that only those
  changed (field-reported).

Check: a before-and-after list of active rules, flows and switches is identical when you
finish.

## 5. The way back

Platform redundancy copies a mistake faithfully: your own backup and the load's result files
are the rollback [WA-R]. Before the production load hold: the result files with new record
Ids (to delete what was inserted), exact prior values of every field an update changes (a
count is not a backup), and the confirmed date of the last data export.

## 6. Reconcile

- Use exact numbers. `sf org list sobject record-counts` is approximate [PLUGIN-LIMITS]:
  reconcile with SOQL counts and control totals (sums of amounts, counts per status, per
  owner, per year).
- Reconcile by the legacy external Id, not by "created today".
- Check relationships (children with no parent), sample records by hand with the client, and
  rollups after the recalculation.
- Before a rule or a flow that classifies existing records goes live, run its condition
  read-only against all of them, count misses and false hits, and explain each (field-reported:
  a rule built from two examples missed about a hundred real cases).
- For a study of production data, count and group; record bodies stay in a private file.

## 7. Privacy

Load only what the purpose needs. Personal data must be "adequate, relevant and limited to
what is necessary", and a processor acts "only on documented instructions from the
controller" and returns or deletes the data at the end [GDPR-5] [GDPR-28]. Keep restricted
data out of sandboxes, or mask it [WA-T].

## Artifacts

| Artifact | Minimum contents |
| --- | --- |
| Mapping specification | Source field, target object and field, transform, default, external Id, load order, owner |
| Load runbook | Sequence, batch sizes, bypass list with restore steps, job ids, rollback criteria |
| Reconciliation report | Counts and control totals per object in source and target, exceptions, sign-off |

Fails when: dirty or duplicate data is loaded as it is; preparation was not budgeted; parents
or owners are missing; parallel batches lock; automation is left off, or left on and sending;
rollups are never recalculated; there are no result files, so no rollback; personal data is
left in spreadsheets or sandboxes.

## Sources

- [TH-PREP] https://trailhead.salesforce.com/content/learn/projects/import-your-data-using-npsp-data-importer/prepare-your-data-for-import
- [TH-IMPORT] https://trailhead.salesforce.com/content/learn/modules/lex_implementation_data_management/lex_implementation_data_import
- [TH-LOAD] https://trailhead.salesforce.com/content/learn/modules/large-data-volumes/load-your-data
- [TH-SCALE] https://trailhead.salesforce.com/content/learn/modules/org-scaling/mitigate-failing-data-loads
- [TH-NPSP] https://trailhead.salesforce.com/content/learn/projects/import-your-data-using-npsp-data-importer/perform-an-import-dry-run
- [TH-ROLLUP] https://trailhead.salesforce.com/content/learn/projects/create-an-opportunity-record-type-for-npsp/customize-rollups-to-exclude-an-opportunity-record-type
- [PLUGIN-DATA] https://raw.githubusercontent.com/salesforcecli/plugin-data/main/README.md
- [BULK-DATE] Bulk API date format, read as a search extract (**verify**): https://developer.salesforce.com/docs/atlas.en-us.api_asynch.meta/api_asynch/datafiles_date_format.htm
- [PLUGIN-LIMITS] https://raw.githubusercontent.com/salesforcecli/plugin-limits/main/README.md
- [DG-RT] https://architect.salesforce.com/docs/architect/decision-guides/guide/record-triggered
- [F-SHARE] https://architect.salesforce.com/docs/architect/fundamentals/guide/platform-sharing-architecture (fetched 2026-11-14)
- [WA-R] https://architect.salesforce.com/docs/architect/well-architected/guide/reliability.html
- [WA-T] https://architect.salesforce.com/docs/architect/well-architected/guide/trust.html
- [GDPR-5] https://gdpr-info.eu/art-5-gdpr/
- [GDPR-28] https://gdpr-info.eu/art-28-gdpr/
- [ADV] Torque advisory catalogue (`torque advisory notes --command TEXT`); entry ids inline.
- field-reported: lessons from real client work with Torque, including a classification of
  273 failed Salesforce CLI calls. Not re-tested for this release.
