# NPSP gotchas, each with a check

"verify" marks practitioner knowledge not confirmed in a primary source.

## Setup

1. **The org may be Nonprofit Cloud, not NPSP.** Check: `sf package installed list`;
   `SELECT COUNT() FROM GiftTransaction` succeeds only in Nonprofit Cloud.
2. **Account, RD and rollup models assumed.** Each changes the rest of the design. Check:
   the three settings queries in SKILL.md.
3. **Enabling a feature by data load is incomplete.** Setting the rollup, RD2 or Gift
   Entry flag skips the metadata deploy, migration and job changes. Check: after
   enablement, `npsp__Rollup__mdt` has rows and CronTrigger shows the new job names.

## TDTM

4. **A deactivated handler comes back after a push upgrade** unless User Managed is on.
   Check: every row with `npsp__Active__c = false` has `npsp__User_Managed__c = true`.
5. **A bypassed load never gets NPSP side effects.** Check after the load:
   `SELECT COUNT() FROM Opportunity WHERE npsp__Primary_Contact__c != null AND Id NOT IN
   (SELECT OpportunityId FROM OpportunityContactRole)`; Health Check for missing payments.
6. **An integration user listed in `Usernames_to_Exclude__c` silently runs without NPSP.**
   Check: `SELECT npsp__Class__c, npsp__Usernames_to_Exclude__c FROM npsp__Trigger_Handler__c
   WHERE npsp__Usernames_to_Exclude__c != null`.
7. **Subscriber flows and NPSP handlers interleave in one save** (verify ordering per
   object). Check: a debug log of one insert shows `TDTM_Opportunity` and the flow
   interview; confirm the fields the flow reads are populated.

## Rollups

8. **Real-time CRLP is skipped under queue pressure** (5 or more Queueables). Check:
   `SELECT COUNT() FROM AsyncApexJob WHERE JobType = 'Queueable' AND Status IN
   ('Queued','Processing','Preparing','Holding')` during loads; compare a donor before and
   after the nightly job.
9. **Soft-credit and GAU rollups are nightly only.** Check: add a soft-credit role; the
   contact's `npsp__Number_of_Soft_Credits__c` changes only after job 04A or Recalculate.
10. **Contact hard credit follows Primary Contact.** Check:
    `SELECT COUNT() FROM Opportunity WHERE npsp__Primary_Contact__c = null AND
    Account.RecordType.DeveloperName = 'HH_Account'` (confirm the record type name).
11. **Rollup failures are silent and notices may go nowhere.** Check: `AsyncApexJob` for
    `CRLP%` classes with `NumberOfErrors > 0`; `npsp__Error__c` volume; the error
    notification recipient is a person who reads it.
12. **Incremental mode leaves time-bound fields stale** for donors with no recent gifts.
    Check: after a definition change or January 1, run the full batch and diff a sample.
13. **Skewed parents need the skew jobs.** Check: all B, C, D jobs exist in CronTrigger;
    count Accounts with `npsp__CustomizableRollups_UseSkewMode__c = true`.
14. **A filter change silently reclassifies donors and Levels.** Check: snapshot Level
    assignments near thresholds, rerun Level Assignment, diff.
15. **A new contact role outside the soft-credit role list is ignored.** Check:
    `SELECT npo02__Soft_Credit_Roles__c FROM npo02__Households_Settings__c` against
    `SELECT Role FROM OpportunityContactRole GROUP BY Role`.
16. **Fiscal-year switches are per feature** (legacy rollups, GAU, each rollup
    definition, RD values). Check: read each and compare with Setup > Fiscal Year.

## Payments and allocations

17. **Closed Lost writes off all unpaid payments.** Check: close-lose a sandbox gift with
    three unpaid payments; all three are written off.
18. **Multi-payment schedules do not follow gift edits or Closed Won.** Check: sum
    unwritten-off payments per open gift against Amount; list won gifts with unpaid payments.
19. **Payment allocations require default allocations and a default GAU.** Check:
    `SELECT npsp__Payment_Allocations_Enabled__c, npsp__Default_Allocations_Enabled__c,
    npsp__Default__c FROM npsp__Allocations_Settings__c`.
20. **Default allocations apply only going forward.** Check: `SELECT COUNT() FROM
    Opportunity WHERE Amount > 0 AND Id NOT IN (SELECT npsp__Opportunity__c FROM
    npsp__Allocation__c)`.
21. **Fixed-amount allocations block reducing a gift.** Check: compare allocation totals
    per gift with Amount before a bulk amount change.

## Recurring donations

22. **RD2 keeps one open installment** (verify); legacy forecast reports break after
    migration. Check: RDs with more than one open installment Opportunity.
23. **Closing an RD closes-lost its open installments** by default. Check:
    `npe03__Open_Opportunity_Behavior__c` against what finance expects.
24. **Unmapped custom Status values make RD2 state unpredictable.** Check: Status Mapping
    lists every picklist value.
25. **Status automation lapses RDs when a processor is merely late.** Check: the days
    settings and RDs set to Lapsed in the last 30 days.
26. **The next-date match range suppresses installments.** Check: for a "missing"
    installment, a manual gift within +/- the range.

## Gift entry and imports

27. **Donation matching default is Do Not Match.** Check:
    `SELECT npsp__Donation_Matching_Behavior__c FROM npsp__Data_Import_Settings__c` and
    the batch value.
28. **Batch Completed, rows Failed.** Check: group `npsp__DataImport__c` in the batch by
    `npsp__Status__c`; read `npsp__FailureInformation__c`.
29. **Gift Entry processing commits hundreds of rows per transaction.** Check: process a
    250-row sandbox batch with every new Payment or Opportunity automation active.

## Households

30. **Refresh Household Names wipes protected manual names.** Check:
    `SELECT COUNT() FROM Account WHERE npo02__SYSTEM_CUSTOM_NAMING__c != null` first.
31. **Contact mailing edits revert to the household default address** unless the Contact
    has an address override. Check: edit a member's street in a sandbox and re-read it
    after the seasonal job.
32. **API deletes leave empty households.** Check: `SELECT COUNT() FROM Account WHERE
    RecordType.DeveloperName = 'HH_Account' AND Id NOT IN (SELECT AccountId FROM Contact)`.
33. **Merges outside NPSP handling can drop soft credits.** Check: survivor gift and soft
    credit counts equal the pre-merge sum.

## Jobs and reporting

34. **Jobs owned by an inactive user stop.** Check: `CreatedBy.IsActive` on `NPSP%` CronTrigger rows.
35. **Pledged and paid are different numbers.** Check: Closed Won Amount by Close Date
    against paid Payment Amount by Payment Date for the same period; explain the gap.
36. **Household totals double count** when hard credit and household-member soft credit
    are added. Check: one household's Account total against the members' soft credits.

## PMM and flows

37. **Service Participants without a Contact produce empty rosters.** Check:
    `SELECT COUNT() FROM pmdm__ServiceParticipant__c WHERE pmdm__Contact__c = null`.
38. **PMM attendance rollups do not move** while feature gates are off. Check: the gate
    records in Setup > Custom Metadata Types; never fix by deploying copies.
39. **A handled flow fault leaves a partial save.** Check: force one failing row in a
    collection Create and count saved rows.
40. **Flow `DATEVALUE()` on a date/time is GMT.** Check: evaluate on an 11 PM local value.
