---
name: salesforce-nonprofit-cloud
description: Work in Salesforce Nonprofit Cloud (Agentforce Nonprofit), the native successor to NPSP. Covers Fundraising, Program and Case Management, Grantmaking, Volunteer Management and Outcome Management, Person Accounts and households, Data Processing Engine rollups, gift entry, licenses and permission sets, metadata versus UI-only setup, gotchas with checks, and NPSP to Nonprofit Cloud migration.
---

# Salesforce Nonprofit Cloud

Nonprofit Cloud (NPC, marketed as Agentforce Nonprofit since late 2025) is native: its
objects are standard objects with no namespace (`GiftTransaction`, not `npsp__...`), built
on Industries components (Person Accounts, Group Membership, Data Processing Engine,
Business Rules Engine, OmniStudio, Action Plans). Features arrive with the seasonal
releases; there is no package to upgrade. NPSP is a different product (see the
`salesforce-npsp` skill); moving from it is re-platforming.

State described: Winter '27, API 68.0. Items marked **verify** could not be confirmed in
a primary source; test them in an org before repeating them to a client.

Reference files in this folder:

- `references/data-model.md`: fundraising objects and lifecycle, households, rollups,
  programs, grantmaking, volunteers, outcomes.
- `references/gotchas.md`: common failure modes, each with a check.
- `references/migration.md`: NPSP to NPC object map, gaps, sequence, reconciliation.

## First checks

| Question | Check |
| --- | --- |
| NPC or NPSP? | `SELECT COUNT() FROM GiftTransaction` succeeds only with NPC Fundraising; `sf package installed list` shows NPSP namespaces |
| Which features are licensed? | Setup > Company Information > Permission Set Licenses: Fundraising Access, Grantmaking, VolunteerManagementPsl, Record Aggregation, Data Pipelines Base User |
| Person Accounts on? | `SELECT IsPersonAccount FROM Account LIMIT 1` compiles |
| Rollups running? | Setup > Data Processing Engine has an active copy of each template in use; Setup > Monitor Workflow Services shows recent Completed runs |
| Commitment processing healthy? | `SELECT COUNT() FROM GiftCommitment WHERE LastNextGenCmtProcError != null` |
| Fundraising settings | `FundraisingConfig` (retrieve it): donor matching, lapsed and failing counts, next-transaction creation, household soft credits |

A plain Developer Edition cannot be switched to NPC. For testing, use the 30-day trial, a
scratch org with the `Fundraising`, `ProgramManagement`, `Grantmaking`,
`VolunteerManagement`, `OutcomeManagement`, `PersonAccounts` and `DataProcessingEngine`
features, or a sandbox of a licensed org.

## Model in brief

- **Money.** `GiftTransaction` is the payment and the unit of revenue (Status defaults to
  Unpaid). `GiftCommitment` plus `GiftCommitmentSchedule` is the promise (recurring gift
  or pledge). Opportunity is optional and means tracked solicitation.
- **Funds.** `GiftDesignation` (one org-wide `IsDefault`), split per transaction by
  `GiftTransactionDesignation`, with defaults from `GiftDefaultDesignation` on a Campaign,
  Commitment or Opportunity.
- **Credit.** `GiftSoftCredit` (role, partial amount or percent); household soft credits
  can be created automatically.
- **People.** Person Accounts for individuals. A household is a business Account plus a
  `PartyRelationshipGroup` of Type Household plus an `AccountContactRelation` per member.
  Households are optional.
- **Rollups.** Data Processing Engine definitions write `DonorGiftSummary` (per donor),
  `GiftDesignation` totals and `OutreachSummary`. Calendar-year only.
- **Entry.** `GiftEntry` rows, alone or in a `GiftBatch`, processed into donors,
  transactions, designations and soft credits. Status and result stay as the audit trail.
- **Programs.** Program, ProgramEnrollment, Benefit, BenefitSchedule, BenefitSession,
  BenefitAssignment, BenefitDisbursement.
- **Grantmaking** (separate license, funder side): FundingOpportunity, ApplicationForm
  (use this; IndividualApplication gets no new enhancements), FundingAward,
  FundingAwardRequirement, FundingDisbursement.
- **Volunteers** (API 64+): VolunteerInitiative, JobPosition, JobPositionShift,
  JobPositionAssignment.
- **Outcomes:** ImpactStrategy, Outcome, IndicatorDefinition, IndicatorAssignment,
  IndicatorResult.

## Licenses and permissions

| Feature | Assign |
| --- | --- |
| Fundraising | `FundraisingAccess` plus permission set group `Fundraising_User` or `Fundraising_Admin` |
| Households and groups | Group Membership; merging needs a clone with Merge and Split Groups |
| Household naming | "Automatic Household Creation and Naming" add-on license |
| Program Management | Program Management permission set; cohorts need Advanced Program Management |
| Grantmaking | Grantmaking Manager, reviewer and Experience Cloud permission sets; every viewer needs a paid license |
| Volunteer Management | Volunteer Management User group; portal and guest sets for sign-up sites |
| Outcome Management | Outcome Management permission set |
| DPE | Data Pipelines Base User (only 3 included, and `Fundraising_Admin` uses one per admin) |

Assign permission set groups and extend with custom or muting sets, not ad hoc object
permissions. Person Account object access comes from Account permissions, field-level
security from Contact fields.

## Metadata versus UI

**Deployable:** objects, fields, layouts, pages, flows, permission sets, `FundraisingConfig`,
`FieldMappingConfig`, `GiftEntryGridTemplate`, `BatchCalcJobDefinition` (DPE),
`RecordAggregationDefinition`, `RelationshipGraphDefinition`, Business Rules Engine
definitions, `ActionPlanTemplate`, `IndustriesSettings`.

**Per-org UI steps:** enabling Person Accounts (irreversible), enabling Data Pipelines
and its Salesforce output connector, assigning permission set licenses, Save As and
Activate on shipped DPE templates, the Default Workflow User, duplicate rule activation,
add-on licenses. Check activation state after any DPE deploy.

**Data, not metadata:** `PartyRoleRelation` roles, designations, source codes, benefit
types, units of measure, programs, decision matrix rows. The Data Import Wizard does not
support NPC objects; use Data Loader, Bulk API or an ETL tool.

## Gotchas to check first

- Nothing rolls up until each DPE template is copied with Save As and the copy activated
  and scheduled.
- Three identities make DPE work: the admin with Data Pipelines Base User, the writeback
  user with full access to written objects, and the Analytics integration user with Read.
- `GiftTransaction` Status defaults to Unpaid; loads that omit it create gifts rollups ignore.
- Commitment Status is computed; loaded values do not stick.
- `ProgramEnrollment.IsActive` defaults to false.
- Donor summary "this year" is calendar year, never fiscal.

Full list with checks: `references/gotchas.md`.

## Sources

- Nonprofit Cloud Developer Guide (objects, fields, actions): https://developer.salesforce.com/docs/atlas.en-us.nonprofit_cloud.meta/nonprofit_cloud/
- Metadata API Developer Guide: https://resources.docs.salesforce.com/latest/latest/en-us/sfdc/pdf/api_meta.pdf
- Scratch org features: https://developer.salesforce.com/docs/atlas.en-us.sfdx_dev.meta/sfdx_dev/sfdx_dev_scratch_orgs_def_file_config_values.htm
- Fundraising editions and permissions: https://help.salesforce.com/s/articleView?id=sfdo.fundraising_editions_and_permissions.htm&type=5
- Security and permissions: https://help.salesforce.com/s/articleView?id=sfdo.npc_nonprofit_cloud_permission_sets.htm&type=5
- Set up Fundraising: https://help.salesforce.com/s/articleView?id=sfdo.fundraising_set_up_fundraising.htm&type=5
- Gift commitment processing: https://help.salesforce.com/s/articleView?id=sfdo.fundraising_gift_commitment_processing.htm&type=5
- DPE limits: https://help.salesforce.com/s/articleView?id=ind.dpe_limits.htm&type=5
- Groups and relationships: https://help.salesforce.com/s/articleView?id=ind.group_membership_how_nonprofit_cloud_models_groups_relationships.htm&type=5
- Community NPC best practices (Salesforce.org community sprints): https://sfdo-community-sprints.github.io/npc-best-practices/
