# Nonprofit Cloud data model

API names from the Nonprofit Cloud Developer Guide, version 68.0
(https://developer.salesforce.com/docs/atlas.en-us.nonprofit_cloud.meta/nonprofit_cloud/).
"(v65)" means the field arrived in API 65.0. **verify** marks unconfirmed claims.

## Fundraising objects

| Object | Role | Key fields |
| --- | --- | --- |
| GiftCommitment | Pledge or recurring gift | `DonorId`, `Status` (Draft, Active, Paused, Failing, Lapsed, Closed; computed), `ScheduleType` (Recurring, Custom), `RecurrenceType` (Open Ended, Fixed Length), `CurrentGiftCmtScheduleId`, `NextTransactionDate`, `WrittenOffAmount`, `LastNextGenCmtProcError` (v67) |
| GiftCommitmentSchedule | A schedule; a new one per change | `TransactionAmount`, `TransactionPeriod` (Daily, Weekly, Monthly, Yearly, Custom), `TransactionInterval`, `TransactionDay` (29 and 30 mean last day in short months), `Type` (Create Transactions, Pause Transactions), `PaymentInstrumentId`, `CampaignId`, `GiftCommitmentSchdBefEditId` |
| GiftCmtChangeAttrLog (v60) | Upgrade and downgrade history | `ChangeStatus`, `ChangeType`, `ChangePerDayAmount`, `CampaignId` |
| GiftTransaction | A payment, paid or expected | `DonorId`, `OriginalAmount` (includes donor-covered fees, excludes processing fees), `Status` (Unpaid default, Pending, Paid, Failed, Canceled, Written-Off, Fully Refunded), `TransactionDate` (required for Paid and Fully Refunded), `TransactionDueDate` (required when linked to a commitment), `PaymentMethod`, `GiftType`, `NonTaxDeductibleAmount`, `MatchingEmployerTransactionId` |
| GiftTransactionDesignation | Transaction to fund split | `GiftDesignationId`, `Amount`, `Percent` |
| GiftDesignation | Fund | `IsDefault` (one org-wide), DPE-written calendar-year totals |
| GiftDefaultDesignation | Default split for new transactions | `ParentRecordId` (Campaign, GiftCommitment or Opportunity), `AllocatedPercentage` |
| GiftSoftCredit | Credit to someone other than the donor | `RecipientId` (Account), `Role` (Soft Credit, Honoree, Household Member, Influencer, Matched Donor, Solicitor, Third Party Donor, Other), `PartialAmount`, `PartialPercent`, `SoftCreditSource` (v68) |
| GiftDefaultSoftCredit (v62) | Soft credits for generated transactions | on GiftCommitment or Opportunity |
| GiftTribute | Honor or memorial | `TributeType`, honoree and notification fields |
| GiftRefund | Refund of a paid transaction | `Amount`, `Date`, `Status` (Initiated, Completed, Failed) |
| PaymentInstrument (v60) | Stored payment method metadata | `Type`, `Last4`, expiry, gateway references; restrict `BankAccountNumber` and `BankCode` |
| GiftEntry, GiftBatch | Staging and batch header | `GiftProcessingStatus` (New, Success, Failure), `GiftProcessingResult`; batch Status includes Partially Processed |
| DonorGiftSummary (v59) | Per-donor rollups written by DPE | `DonorId`, totals this, last and two years ago, first, last, largest gift, soft-credit totals, RFM scores, `GivingLevel`, volunteer hours |
| OutreachSourceCode, OutreachSummary | Appeal code under a Campaign and its rollups | `SourceCode` (org-unique), UTM fields |

### Lifecycles

- One-time gift through Gift Entry: created Paid with a transaction date.
- Recurring: the engine creates the next transaction as Unpaid with a due date when
  `ShouldCreateRcrSchdTrxn` is on; processor or staff sets Paid or Failed.
- Commitment status: Lapsed when consecutive past unpaid transactions reach
  `LapsedUnpaidTrxnCount`; Failing when consecutive failures reach
  `FailedTransactionCount` (0 disables); Closed when nothing is scheduled or unpaid and
  `ShouldClosePaidRcrCmt` is on; Paused when the current schedule is Pause Transactions.
- Upgrade or downgrade writes a new schedule and a change log row; the old schedule is kept.
- Refund: a GiftRefund row. A partial refund leaves Status Paid with `IsPartiallyRefunded`;
  a full refund needs Status Fully Refunded. The documented meaning of `IsPaid` and
  `CurrentAmount` is odd; **verify** by creating paid, partially refunded and fully
  refunded rows.
- Designation precedence when several parents carry defaults: **verify** in the org.

### Actions and APIs

Invocable actions (API 59+): `processGiftEntries` (with `isDryRun`),
`manageRcrGiftCmtSchd`, `manageCustomGiftCmtSchds` (up to 15 schedules per call),
`manageGiftDefaultDesignations`, `manageGiftTrxnDesignations`, `closeGiftCommitment`,
`pauseGiftCommitmentSchedule`, `resumeGiftCommitmentSchedule`, `processGiftCommitment`,
`updateProcessedGiftEntries`. Connect APIs under `/connect/fundraising/` serve donation
platform integrations.

### Gift entry

Donor matching uses `FundraisingConfig.DonorMatchingMethod`: Duplicate_Management_Rules
(default) or No_Matching, optionally an external ID field. Gift Entry names records
`{Donor}-{Amount}-{Date}`; direct creation has no naming, and `Name` is required on
GiftTransaction. A processed entry cannot be re-batched. Field mapping is metadata
(`FieldMappingConfig`).

## Households and relationships

A household is four kinds of record: a business Account; a `PartyRelationshipGroup` with
`Type = Household` (the default Type is Group); the member Person Accounts; one
`AccountContactRelation` per member ("Contacts to Multiple Accounts" must be on).
Person-to-person links use `ContactContactRelation`, org links `AccountAccountRelation`,
each pointing to a `PartyRoleRelation` you load (none ship). Custom flows that create
households must create the group record too.

Household naming (Winter '27, add-on license): `HouseholdNamingConfig` with a name
pattern formula, conjunction, maximum members 1 to 9 (default 2); per-household opt-out
`IsExclFrHshldAutoNaming`. **verify** the formula tokens in Setup.

Addresses are `ContactPointAddress`, including seasonal start and end fields.

## Rollups (Data Processing Engine)

Shipped fundraising templates: DonorGiftSummary, GiftDesignation, OutreachSummary, plus
RFM and an actionable list template. Program templates (enrollment aggregates,
disbursement aggregates, attendance rate) are not scheduled by default.

Setup order: assign Data Pipelines Base User; set the Default Workflow User (the
writeback user, full access to written objects); give the Analytics Cloud Integration
User Read on every object and field read; enable Data Pipelines and the Salesforce output
connector; Save As each template with a release name and Activate the copy; schedule with
schedule-triggered flows or the Manage Fundraising DPE Definitions flow action (wait on
the Batch Job Status Changed Event with a timeout under 24 hours); show results with the
Related Record Detail Display component.

Limits: 50 active definitions; 30 runtime hours and 10 million rows per month; 1 GB
writeback per 24 hours. Space overlapping runs at least 15 minutes and order dependent
ones. Monitor at Setup > Monitor Workflow Services (30 days). SAQL filters are
case-sensitive. Alternatives: Record Rollups (`RecordAggregationDefinition`) and
flow-based rollups.

DonorGiftSummary rows were observed (2024) to be deleted and reinserted each run, so
anything keyed to their Ids must run after DPE (**verify** current behavior).

## Program and Case Management

| Object | Notes |
| --- | --- |
| Program | `Status`, `ParentProgramId`, DPE-written enrollee counts |
| ProgramEnrollment | `Status` (Applied default, In Progress, Waitlisted, Denied, Withdrawn, Completed), `IsActive` default false, `IsAnonymous`, attendance counts (v65) |
| Benefit, BenefitType, UnitOfMeasure | What is delivered and in which unit |
| BenefitAssignment | Person to benefit; requires `ParentRecordId` (CarePlan, GoalAssignment, IndividualApplication or ProgramEnrollment) |
| BenefitSchedule, RecurrenceSchedule, BenefitScheduleAssignment | Planned pattern, capacity, participants |
| BenefitSession | One occurrence; Status Scheduled, Completed, Postponed, Cancelled |
| BenefitDisbursement | Delivery; `DisbursementStatus` (Enrolled, Completed, Absent, Excused), `RecipientType` (Program Enrollment, Walk-in, Anonymous) |
| ProgramCohort, ProgramCohortMember | Advanced Program Management license |
| Case management | CarePlan, GoalAssignment, Interaction, InteractionSummary, Referral |

Walk-ins: Add Participant on a Benefit Session creates the enrollment, assignment and
disbursement if missing. Anonymous service: an anonymous, active enrollment and an ad hoc
bulk disbursement with Recipient Type Anonymous. Protect sensitive program data with
Compliant Data Sharing and least-privilege case teams.

## Grantmaking

Two application models: ApplicationForm (v66, use for new builds) and the older
IndividualApplication. No out-of-box automation creates the award from an approved
application; there is automation to schedule disbursements and requirements on an award.
`FundingAwardRequirement` types: Narrative, Financial, Combined Report, Contract;
disbursing only after requirements are met is a process you enforce. Grant seeking
(inbound grants) belongs to Fundraising: Opportunity plus commitment plus transactions.

## Volunteer Management

VolunteerInitiative (the effort, rollups for hours and filled or open assignments),
Position (reusable role), JobPosition, JobPositionShift (`TimeZone` required, capacity),
JobPositionAssignment (Status Upcoming through Complete, Absent, Canceled; actual
duration in hours). Volunteer stats also land on DonorGiftSummary. Sign-up sites use
Experience Cloud portal and guest permission sets; review guest exposure.

## Outcome Management

ImpactStrategy, Outcome, OutcomeActivity (links an outcome to, for example, a Program),
IndicatorDefinition, IndicatorAssignment, IndicatorPerformancePeriod, IndicatorResult
(Manual or AutomaticallyCalculated, with the calculating flow recorded). Enable with the
`enableOutcomes` setting.

## Reporting

Revenue reports use GiftTransaction (Status Paid, TransactionDate), not Opportunity.
Remove the standard Campaign statistics section, which counts Opportunities. For fiscal
years, report on GiftTransaction with fiscal date filters or build a custom rollup.
In-kind transactions count in donor summaries unless excluded.
