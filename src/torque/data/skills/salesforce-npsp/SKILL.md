---
name: salesforce-npsp
description: Work in a Nonprofit Success Pack (NPSP) org, including the Program Management Module and Outbound Funds. Covers the data model, households, TDTM trigger handlers, customizable rollups, enhanced recurring donations, payments, GAU allocations, soft credits, gift entry, PMM attendance, flows on NPSP objects, and the common gotchas with a check for each.
---

# Salesforce NPSP

NPSP is a suite of managed packages on standard Account, Contact and Opportunity. It is
still supported, but new nonprofit features ship in Nonprofit Cloud, which is a different
product (see the `salesforce-nonprofit-cloud` skill). Moving between them is a migration,
not an upgrade.

Reference files in this folder, read when the task needs them:

- `references/mechanics.md`: TDTM, rollup engines and jobs, recurring donations,
  payments, allocations, soft credits, gift entry, households, scheduled jobs.
- `references/pmm-ofm.md`: Program Management Module objects and attendance, rollup
  gates, Outbound Funds.
- `references/gotchas.md`: the common failure modes, each with an org check.

## First checks in an unfamiliar org

Confirm these before designing anything; the answers change most other advice.

| Question | Read-only check |
| --- | --- |
| NPSP or Nonprofit Cloud? | `sf package installed list --target-org ALIAS` shows `npsp`, `npe01`, `npo02`, `npe03`, `npe4`, `npe5`. SOQL on `InstalledSubscriberPackage` by namespace is unreliable. `SELECT COUNT() FROM GiftTransaction` succeeds only in Nonprofit Cloud. |
| Account model | `SELECT npe01__Account_Processor__c FROM npe01__Contacts_And_Orgs_Settings__c` (Household Account is the default and current model) |
| Recurring donation model | `SELECT npsp__IsRecurringDonations2Enabled__c FROM npe03__Recurring_Donations_Settings__c` |
| Rollup engine | `SELECT npsp__Customizable_Rollups_Enabled__c FROM npsp__Customizable_Rollup_Settings__c` |
| Scheduled jobs alive | `SELECT CronJobDetail.Name, State, NextFireTime, CreatedBy.IsActive FROM CronTrigger WHERE CronJobDetail.Name LIKE 'NPSP%'` |
| Errors and who hears about them | `SELECT npsp__Store_Errors_On__c, npsp__Error_Notifications_On__c, npsp__Error_Notifications_To__c, npsp__Disable_Error_Handling__c FROM npsp__Error_Settings__c`; recent `npsp__Error__c` rows |
| Handlers switched off | `SELECT npsp__Class__c, npsp__Object__c, npsp__Active__c, npsp__User_Managed__c, npsp__Usernames_to_Exclude__c FROM npsp__Trigger_Handler__c WHERE npsp__Active__c = false OR npsp__Usernames_to_Exclude__c != null` |
| PMM or Outbound Funds present | `pmdm` or `outfunds` in the installed package list |

Then run NPSP Settings > System Tools > Health Check. Read field and namespace names
from the org with describe; installed versions differ.

## Packages

| Namespace | Owns |
| --- | --- |
| `npe01` | Payment (`npe01__OppPayment__c`), Contacts and Orgs settings, payment field mappings |
| `npo02` | Households settings, legacy household object, legacy rollup fields on Account and Contact |
| `npe03` | Recurring Donation (`npe03__Recurring_Donation__c`) and its settings |
| `npe4` | Relationship (`npe4__Relationship__c`) |
| `npe5` | Affiliation (`npe5__Affiliation__c`) |
| `npsp` | Everything else: TDTM, Address, GAU and Allocation, Partial and Account Soft Credit, Levels, Engagement Plans, Data Import and Gift Entry, rollup custom metadata, Error log, RD2 schedules and change log |

## Data model in brief

- **Households.** Each individual Contact sits in a Household Account. A person's
  employer link is an Affiliation, not a move of the Contact to the company Account.
  Person-to-person links are Relationships with automatic reciprocals.
- **Gifts.** The Opportunity is the gift. `npsp__Primary_Contact__c` drives the primary
  contact role and contact hard credit; a blank Primary Contact means no contact hard credit.
- **Payments** (`npe01__OppPayment__c`, master-detail to Opportunity) are the cash.
  Opportunity Amount and Close Date are the commitment. Pledged and paid totals differ.
- **GAU allocations** (`npsp__Allocation__c`) split a gift, payment, campaign or
  recurring donation across `npsp__General_Accounting_Unit__c` funds.
- **Soft credits.** Contact roles whose role is in the soft-credit role list
  (default `Matched Donor;Soft Credit;Household Member`) give full-amount soft credit.
  `npsp__Partial_Soft_Credit__c` gives a partial amount; `npsp__Account_Soft_Credit__c`
  credits an organization.
- **Recurring donations.** Enhanced recurring donations (RD2) add
  `npsp__RecurringDonationSchedule__c` and `npsp__RecurringDonationChangeLog__c`.
- **Addresses** (`npsp__Address__c`) belong to the household; the default address syncs
  to the Account billing address and every member's mailing address.

## Automation and extension

- **TDTM.** One trigger per object dispatches `npsp__Trigger_Handler__c` rows in load
  order. To keep a handler off across upgrades, set Active off and User Managed on.
  Per-user skip: `Usernames_to_Exclude__c`. Per-transaction bypass in Apex:
  `npsp.TDTM_Config_API` or the `Callable_API` action `TDTM.DisableAllTriggers`. Any
  bypass skips contact roles, payments, households, allocations and rollups for those
  rows; run the matching batch afterwards.
- **Custom Apex in NPSP's order:** a global class extending `npsp.TDTM_Runnable` plus a
  `npsp__Trigger_Handler__c` row with User Managed on (confirm the signature against the
  installed version).
- **UI or Callable only:** enabling customizable rollups, RD2, Advanced Mapping and Gift
  Entry. Setting the custom setting by data load skips the metadata deploy, migration and
  job rescheduling.
- **Do not** deploy edited copies of NPSP or PMM custom metadata records, delete default
  handler rows, or modify skew and rollup key fields.

## Flows on NPSP objects

- Your record-triggered flow and NPSP's handlers run in the same save. Check in a debug
  log that the fields your flow reads (Primary Contact, contact roles, payments) are set at
  the point your flow runs.
- Gift Entry and the Data Importer commit hundreds of rows in one transaction. Automation
  on Opportunity, Payment or Allocation must be bulk-safe: no queries or DML in loops,
  one collection DML per object. Test with a 250-row batch.
- Creating Contacts in a flow makes NPSP create a Household Account for each in the same
  transaction, which spends limits your flow does not see.
- Validation rules on Account and Contact can fail NPSP's rollup updates silently (the
  error lands in `npsp__Error__c`). Give such rules a bypass for automated contexts.

Platform lessons that bite in NPSP and PMM builds, each observed in a developer org:

1. **A handled fault on a collection Create can partially save.** With a fault
   connector, the rows that succeeded stay saved and the fault path runs; IDs are not
   written back to the collection. Only an unhandled fault rolls back the whole
   transaction. For all-or-nothing, use a Roll Back Records element on the fault path
   (before logging, or the log is rolled back too). Check: create three records where one
   fails a validation rule, then count what was saved.
2. **Flow formula `DATEVALUE()` on a date/time uses GMT.** An evening session in a
   US time zone dates to the next day, and a late December 31 value lands in the next
   year. Use a date formula field on the object, which evaluates in the user's time zone,
   or adjust explicitly. Check: evaluate the formula on a record timestamped 11 PM local.
3. **Subflow elements have no fault connector.** Handle faults inside the subflow and
   return an error output the parent checks. Check: the Subflow element in Flow Builder
   offers no Add Fault Path.
4. **Newly deployed custom fields have no field-level security until granted.** A
   metadata deploy without a permission set grant leaves the field invisible, and SOQL
   reports "No such column". Deploy a permission set with the field permissions in the
   same change. Check: `SELECT Field, PermissionsRead FROM FieldPermissions WHERE
   Field = 'Object__c.Field__c'`.
5. **Never deploy subscriber copies of managed custom metadata records** (for example
   PMM feature gates or NPSP rollup definitions). A copy without the namespace on the
   record name creates a duplicate DeveloperName that can make the package's own trigger
   fail on every insert. Edit managed records in Setup; remove a stray copy with a
   destructive deploy. Check: query the type for duplicate DeveloperName values.
6. **Flow Builder debug runs commit unless rollback mode is on.** A debug run of an
   autolaunched or screen flow writes real records when the rollback option is cleared.
   Check: after any debug run, query for records it could have created and delete them by Id.

## Surfaces

| Task | Supported path |
| --- | --- |
| Recalculate one donor | Recalculate Rollups button (async, minutes) |
| Recalculate everyone | NPSP Settings > Bulk Data Processes > Rollup Donations Batch |
| Backfill payments or default allocations | Create Missing Payments; Batch Create Default Allocations |
| Add a customizable rollup | Configure Rollups UI, filter group first |
| Add a field to Gift Entry | Data Import field, Advanced Mapping entry, template field |
| Turn on RD2 | Enablement wizard with a dry run first; treat as one-way (verify) |

## Sources

- NPSP source (behavior and defaults): https://github.com/SalesforceFoundation/NPSP
- NPSP data model: https://developer.salesforce.com/docs/platform/data-models/guide/nonprofit-success-pack.html
- Customizable rollup job modes: https://help.salesforce.com/apex/HTViewHelpDoc?id=sfdo.NPSP_Custom_Rllps_jobs.htm&language=en_us
- Configure recurring donations: https://help.salesforce.com/s/articleView?id=sfdo.npsp_configure_recurring_donations.htm&type=5
- Configure Gift Entry: https://help.salesforce.com/s/articleView?id=sfdo.npsp_gift_entry.htm&type=5
- Manage trigger handlers: https://help.salesforce.com/s/articleView?id=sfdo.NPSP_Manage_Trigger_Handlers.htm&type=5
- PMM: https://help.salesforce.com/s/articleView?id=sfdo.pmm_overview.htm&type=5
