# NPSP mechanics

Defaults below come from the NPSP source (https://github.com/SalesforceFoundation/NPSP).
Items marked "verify" are practitioner knowledge; confirm them in the org before relying
on them. Read org values rather than assuming defaults.

## TDTM

- `npsp__Trigger_Handler__c` fields: `Active__c`, `Asynchronous__c`, `Class__c`,
  `Load_Order__c`, `Object__c`, `Trigger_Action__c` (for example
  `BeforeInsert;AfterUpdate`), `User_Managed__c`, `Usernames_to_Exclude__c`.
- About 55 default rows ship. Examples on Opportunity: contact roles (order 0), legacy
  rollups and payments (1), allocations and RD2 (2), campaign members (3), customizable
  rollups and partial soft credits (4).
- Install and upgrade re-sync default rows. A row deactivated without User Managed comes
  back on the next push upgrade.
- Supported `Callable_API` actions (`Type.forName('npsp', 'Callable_API')`):
  `TDTM.DisableAllTriggers`, `Opp.MapStageToState`, `CRLP.IsCrlpEnabled`,
  `RD2.ExecuteDataMigration`, `RD2.Pause`, `RD2.QuerySchedules`, `RD2.QueryInstallments`,
  `Settings.EnableEnhancedRecurringDonations`, `Settings.EnableCustomizableRollups`,
  `Settings.EnableAdvancedMapping`, `Settings.EnableGiftEntry`, `Apex.ScheduleJob` (NPSP
  schedulables only), `PMT.ProcessRefunds`. Others are internal.

## Rollups

**Legacy rollups** write `npo02__*` fields in real time (`RLLP_OppRollup_TDTM`) plus
nightly jobs `NPSP 01` to `NPSP 05`. Filters live in `npo02__Households_Settings__c`.

**Customizable rollups (CRLP)** store definitions as `npsp__Rollup__mdt`,
`npsp__Filter_Group__mdt` and `npsp__Filter_Rule__mdt`, written by the Configure Rollups UI.

- Operations: Count, Sum, Average, Largest, Smallest, First, Last, Years_Donated,
  Donor_Streak, Best_Year, Best_Year_Total. Time bounds: All_Time, Years_Ago, Days_Back.
  Each definition has its own fiscal-year flag.
- Real time: `CRLP_Rollup_TDTM` on Opportunity and Payment enqueues a Queueable for the
  Account, Primary Contact and recurring donation. It is skipped, with no error, when 5 or
  more Queueable jobs are already queued or running in the org.
- Soft-credit and GAU rollups are not real time; they change in the nightly jobs or on
  Recalculate.
- Nightly jobs (org time): 01A Account hard credit 23:00, 02A Contact hard credit 23:05,
  03A account-level contact soft credit and 03B account soft credit 23:10, 04A contact
  soft credit 23:15, 05 GAU 23:20, 06A recurring donations 23:25; B, C and D variants are
  skew jobs.
- Skew: parents with more than 250 related records (setting) or flagged
  `CustomizableRollups_UseSkewMode__c` are processed only by the skew jobs.
- Incremental mode (default on for Account and Contact hard credit) recalculates only
  parents with recently changed gifts. After a definition change or a year rollover, run
  the full Rollup Donations Batch.
- Failures (validation rules, locking, limits on skewed parents) go to `npsp__Error__c`,
  not to the user. Monitor `AsyncApexJob` where `ApexClass.Name LIKE 'CRLP%'`.

## Recurring donations

**Legacy (RD1)** pre-creates open installments for `npe03__Opportunity_Forecast_Months__c`
(default 12) ahead. Job: `NPSP 06 - Recurring Donation Updates`.

**Enhanced (RD2)**, job `NPSP 06 - Enhanced Recurring Donation Updates` (22:00):

- Fields: `RecurringType__c` (Open, Fixed), `InstallmentFrequency__c`, installment period
  (Monthly, Yearly, Weekly, Daily, 1st and 15th), `Day_of_Month__c`, `StartDate__c`,
  `Status__c` (Active, Lapsed, Closed, Paused), `ClosedReason__c`, `PaymentMethod__c`.
- Creates the next installment Opportunity, not a 12-month forecast (verify the count in
  the org). `InstallmentOppAutoCreateOption__c`: create next (default), disable first,
  disable all.
- An existing Opportunity within +/- `NextDonationDateMatchRangeDays__c` (default 3) of
  the next date suppresses the new installment.
- Closing an RD applies `npe03__Open_Opportunity_Behavior__c` (default: mark open
  installments Closed Lost, which then writes off their unpaid payments).
- Status automation moves an RD to Lapsed, then Closed, after N days past an unpaid
  installment. Every Status value, including custom ones, must be mapped to a state in
  `RecurringDonationStatusMapping__mdt`.
- Pause and future-dated edits create `RecurringDonationSchedule__c` rows. The change log
  (`EnableChangeLog__c`, default off) records upgrades and downgrades.
- Deleting an RD deletes its open installments and is blocked if any installment is
  Closed Won.
- Migration from RD1: run the dry run batch, read errors in `npsp__Error__c`, then
  migrate. Rebuild forecast reports afterwards.

## Payments

- With payments enabled, a new Opportunity with an Amount gets one payment; Closed Won
  marks that single payment paid.
- Amount or Close Date edits sync only when there is exactly one unpaid payment that
  matches the old values. Multi-payment schedules do not follow edits and are not marked
  paid on Closed Won.
- Closed Lost writes off every unpaid payment (`npe01__Written_Off__c = true`).
- `Enforce_Accounting_Data_Consistency__c` requires dates by payment state.
- Refunds are negative payments linked by `npsp__OriginalPayment__c`.

## Allocations

- An allocation has one parent (Opportunity, Payment, Campaign or Recurring Donation) and
  an amount or percent. Campaign and RD allocations are templates for new gifts.
- Default allocations send any unallocated remainder to the default GAU, going forward
  only; backfill with Batch Create Default Allocations.
- Payment allocations require default allocations on with a default GAU.
- Percent allocations rescale when the gift amount changes; fixed amounts do not, and a
  reduction below their total is blocked.

## Gift entry and data import

- Gift Entry, the retired batch tools and the Data Importer all stage rows in
  `npsp__DataImport__c` under `npsp__DataImportBatch__c`.
- Order: My Domain, then Advanced Mapping, then Gift Entry. Unmapped staging fields are
  ignored silently.
- Contact matching default is first name, last name and email; case and whitespace
  differences create duplicates.
- Donation matching default is Do Not Match, so imported payments for open pledges
  create new gifts. Other options: No Match, Single Match, Single Match or Create, Best
  Match, Best Match or Create.
- A Completed batch can hide Failed rows; group rows by `npsp__Status__c` and read
  `npsp__FailureInformation__c`.

## Households

- Naming formats in `npsp__Household_Naming_Settings__c` (default `{!LastName} Household`);
  `{!{!Field}}` repeats across members.
- A manual edit to the name or a greeting is protected via
  `npo02__SYSTEM_CUSTOM_NAMING__c`; typing `REPLACE` restores automatic naming. Refresh
  Household Names overwrites all names irreversibly.
- Contact Overrun Count default differs between the code (9) and training material (2);
  read the org value.
- Contact merge through NPSP or the standard merge fires `CON_ContactMerge_TDTM`, which
  fixes households and roles. Deleting the last Contact by API leaves an empty household;
  the UI override offers to delete the Account.

## Scheduled jobs (defaults, org time zone)

| Job | Schedule |
| --- | --- |
| NPSP 00 - Error Processing | hourly |
| NPSP 01 to 06 rollups (legacy or customizable) | 23:00 to 23:30 |
| NPSP 06 recurring donation updates | 22:00 |
| NPSP 07 - Seasonal Address Updates | 23:00 |
| NPSP 08 - Level Assignment Updates | 04:00 |
| NPSP 09 - Data Import Batch Processing | 22:00 |

Jobs owned by an inactive user stop. `Don_t_Auto_Schedule_Default_NPSP_Jobs__c` on
`npsp__Error_Settings__c` stops NPSP re-creating missing jobs.

## Reporting

- Cash: Payment Amount and Payment Date where Paid. Commitment: Opportunity Amount and
  Close Date. Funds: Allocation totals, which differ from gift totals when gifts are
  unallocated or excluded.
- Household giving is the Account hard credit. Adding members' "Household Member" soft
  credits to it counts the same gift twice.
- The "This Year" report filter is calendar; use "Current FY" for fiscal reporting. Each
  rollup feature has its own fiscal-year setting, and RD fiscal values support standard
  fiscal years only.
