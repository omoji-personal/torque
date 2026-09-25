# NPSP to Nonprofit Cloud migration

Salesforce publishes capability translations but no field-level mapping. Field mappings
below are design decisions; confirm every field with describe in both orgs. An in-place
conversion of an NPSP org is not documented; plan for a new NPC org or sandbox
(**verify** with Salesforce if a client asks for in-place).

## Design-changing findings

Raise these in discovery; each changes the design or every number.

1. **Who is the donor (DonorId).** NPSP hard-credits the household Account; NPC's
   `DonorId` can be a person, household or organization Account. Recommended: the person
   Account for individual gifts, with household soft credits and household totals
   expressing the household view. Document the choice; it changes every donor summary.
2. **Households become optional.** NPSP makes one per contact; migrate only households
   used for giving or programs, one per person.
3. **Household soft credits: one source only.** Migrate NPSP "Household Member" contact
   roles with NPC's automatic household soft credits off, or drop them and let NPC create
   them going forward. Both doubles every household credit.
4. **Do not migrate future open installments.** NPC generates the next transaction
   itself; migrated open ones duplicate it and, left unpaid, push commitments to Lapsed.
5. **Commitment status is recalculated from history.** Old unpaid installments flip
   commitments to Lapsed or Failing right after load; write them off, cancel them or leave
   them out.
6. **Revenue moves from Opportunity to GiftTransaction.** One-time gifts usually lose
   their Opportunity; keep Opportunities only for solicitation (major gifts, inbound grants).
7. **Rollups become calendar-year DPE output.** CRLP filter groups and fiscal-year
   rollups have no equivalent; rebuild fiscal reporting on GiftTransaction or a custom
   rollup, and expect donor totals to differ.
8. **Grantmaking has no application-to-award automation,** and IndividualApplication is
   frozen; budget both for Outbound Funds clients.
9. **Seasonal addresses are supported** (ContactPointAddress), contrary to some partner
   material.

## Object map

| NPSP | NPC |
| --- | --- |
| Contact (individual) | Person Account |
| Household Account | Business Account + PartyRelationshipGroup (Type Household) + AccountContactRelation per member |
| Organization Account | Business Account |
| `npe4__Relationship__c` | ContactContactRelation + PartyRoleRelation |
| `npe5__Affiliation__c` | AccountContactRelation (keep "primary affiliation" as a custom field; do not map it to `IsPrimaryGroup`) |
| `npsp__Address__c` | ContactPointAddress |
| Opportunity, one-time Closed Won | GiftTransaction (Paid) |
| Opportunity, cultivated pledge or grant | Opportunity + GiftCommitment + GiftTransactions |
| `npe01__OppPayment__c` | GiftTransaction (one per payment) |
| `npe03__Recurring_Donation__c` | GiftCommitment (Recurring) + PaymentInstrument |
| `npsp__RecurringDonationSchedule__c` | GiftCommitmentSchedule (pause rows become Pause Transactions) |
| `npsp__RecurringDonationChangeLog__c` | GiftCmtChangeAttrLog (partial fit) |
| `npsp__General_Accounting_Unit__c` | GiftDesignation |
| `npsp__Allocation__c` on gift or payment | GiftTransactionDesignation |
| `npsp__Allocation__c` on Campaign or RD | GiftDefaultDesignation |
| Default GAU | GiftDesignation with `IsDefault = true` |
| Soft-credit contact roles, Partial and Account Soft Credit | GiftSoftCredit |
| Tribute fields | GiftTribute |
| Refund payments | GiftRefund on the original transaction |
| `npsp__DataImport__c`, batches | GiftEntry, GiftBatch (usually do not migrate history) |
| Engagement Plans | Action Plan Templates |
| Levels | `DonorGiftSummary.GivingLevel` or custom |
| Customizable rollups | DPE definitions, Record Rollups or flows |
| PMM Program, Service, Service Schedule, Service Session | Program, Benefit, BenefitSchedule, BenefitSession |
| PMM Program Engagement, Service Participant, Service Delivery | ProgramEnrollment, BenefitAssignment (+ BenefitScheduleAssignment), BenefitDisbursement |
| Volunteers for Salesforce campaign, job, shift, hours | VolunteerInitiative, JobPosition, JobPositionShift, JobPositionAssignment |
| Outbound Funds program, request, requirement, disbursement, review | FundingOpportunity, ApplicationForm + ApplicationDecision + FundingAward, FundingAwardRequirement, FundingDisbursement, ApplicationReview |

## No equivalent

| NPSP feature | Option |
| --- | --- |
| Configurable Levels | Custom field plus custom DPE or flow |
| CRLP filter groups, fiscal-year rollups | Custom DPE or report-level fiscal filters |
| Multi-object Data Importer with Advanced Mapping | ETL or Bulk API |
| Twice-monthly recurring | Two schedules or a weekly approximation |
| RD closed reason | Custom field on GiftCommitment |
| Affiliation primary flag | Custom field or role convention |
| Relationship reciprocal picklist | PartyRoleRelation table you define |
| Matching gift status fields | `MatchingEmployerTransactionId` plus custom fields |
| Volunteer recurrence schedule | None |
| Outbound Funds expenditures to GAUs | Custom |

## Key field transforms

- Payment amount to `OriginalAmount` (gross including donor cover); paid to Status Paid,
  written off to Written-Off, unpaid future to Unpaid; payment date to `TransactionDate`;
  scheduled date to `TransactionDueDate`.
- Payment method values mapped to the NPC picklist; avoid custom values until tested.
- Fair market value to `NonTaxDeductibleAmount`; matching gift link to
  `MatchingEmployerTransactionId` in a second pass.
- Opportunity-level allocations on multi-payment gifts are prorated per payment; payment
  allocations win when both exist. Sums must equal the amount.
- A gift with no payments becomes one transaction from Amount and Close Date.
- Carry every source Id in a `Legacy_..._Id__c` external ID field on the target.

## Sequence

1. **Assess:** account model, RD model, rollups in use, GAU defaults, soft-credit
   settings, Levels, Engagement Plans, integrations (donation platforms must support NPC
   APIs), automations, reports. Decide DonorId, the household rule, fiscal reporting and
   which Opportunities remain.
2. **Build the target:** licenses, Person Accounts, Fundraising, Contacts to Multiple
   Accounts, Group Membership, `FundraisingConfig`, picklists, duplicate rules,
   designations (one default), PartyRoleRelation, source codes, program reference data,
   DPE copies activated but not scheduled, external ID fields.
3. **Freeze and extract** the source, with a delta plan for cutover.
4. **People:** users, organizations, person accounts, household accounts, group records,
   household relations, affiliations, contact and account relations, addresses.
5. **Fundraising reference:** campaigns, source codes, designations, kept Opportunities,
   default designations.
6. **Commitments with automation off:** snapshot, then turn off next-transaction creation
   and household soft credits (verify their bulk-insert behavior in scratch); load payment
   instruments, commitments, schedules, default designations and soft credits.
7. **Money:** historical transactions, designations, soft credits, tributes, refunds (then
   Fully Refunded status), matching links.
8. **Programs, volunteers, grants** if in scope; set `ProgramEnrollment.IsActive` explicitly.
9. **Restore settings** exactly; let processing create next transactions; check
   `LastNextGenCmtProcError`.
10. **Run DPE**, then schedule the nightly runs 15 minutes apart.
11. **Reconcile, test as real users, cut over,** and restore every bypassed validation and
    duplicate rule.

Tools: Bulk API 2.0, Data Loader or ETL with upsert on external IDs; SFDMU or CumulusCI
for repeatable dry runs. Not the Data Import Wizard.

## Reconciliation

Run each check in both orgs and keep both numbers. Reconcile by legacy ID.

| Check | NPSP | NPC |
| --- | --- | --- |
| Individuals | individual Contacts | `SELECT COUNT() FROM Account WHERE IsPersonAccount = true` |
| Households in scope | Household Accounts | `SELECT COUNT() FROM PartyRelationshipGroup WHERE Type = 'Household'` |
| One household each | n/a | contacts with more than one household relation = 0 |
| Paid revenue per calendar year | paid payment amounts by payment date year | `SELECT CALENDAR_YEAR(TransactionDate), SUM(OriginalAmount) FROM GiftTransaction WHERE Status IN ('Paid','Fully Refunded') GROUP BY CALENDAR_YEAR(TransactionDate)`, net of refunds per policy |
| Status counts | paid and written-off payments | `SELECT Status, COUNT(Id) FROM GiftTransaction GROUP BY Status` |
| Crosswalk | every payment Id | each legacy payment Id present exactly once |
| Fund totals | allocations by GAU | designation amounts by designation, paid transactions |
| Designation completeness | allocations equal amount | transactions whose designations do not sum to the amount = 0 |
| Soft credits | partial plus role-based per contact | soft credit amount per recipient |
| Active recurring and monthly value | active RDs, monthly equivalent amount | commitments by Status; current schedule monthly equivalent |
| Next installments | next payment date per RD | one future Unpaid transaction per active commitment |
| Processing health | n/a | commitments with `LastNextGenCmtProcError` = 0 |
| Donor totals | 50-donor sample of rollup totals | same donors' DonorGiftSummary totals after DPE; explain differences (filters, DonorId, calendar year) |
| Settings restored | pre-load snapshot | `FundraisingConfig`, duplicate and validation rules equal the snapshot |
| End user | n/a | a fundraiser (not an admin) enters a gift, sees the donor summary, runs the board report |

## Sources

- Nonprofit Cloud Developer Guide: https://developer.salesforce.com/docs/atlas.en-us.nonprofit_cloud.meta/nonprofit_cloud/
- NPSP source: https://github.com/SalesforceFoundation/NPSP
- Community NPSP-to-NPC translations (Salesforce.org community sprints): https://sfdo-community-sprints.github.io/npc-best-practices/
- Gift commitment processing: https://help.salesforce.com/s/articleView?id=sfdo.fundraising_gift_commitment_processing.htm&type=5
