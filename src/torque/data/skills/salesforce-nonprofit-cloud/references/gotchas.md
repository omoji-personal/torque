# Nonprofit Cloud gotchas, each with a check

**verify** marks claims not confirmed in a primary source.

## Product and licensing

1. **NPC is not an NPSP upgrade.** Check: `sf package installed list` for NPSP
   namespaces and `SELECT COUNT() FROM GiftTransaction` for NPC.
2. **A plain Developer Edition cannot become NPC.** Check: no Fundraising Access
   permission set license in Company Information; the GiftTransaction query errors.
3. **Scratch capacity from a Developer Edition Dev Hub is small** (3 active, 6 per day,
   30 days maximum; enabling Dev Hub is permanent). Check: `sf org list --all`.
4. **`NonprofitCloudCaseManagementUser` is the legacy managed package license,** not
   native case management (which comes with `ProgramManagement`).
5. **Trial orgs are demo-configured.** Check: diff `FundraisingConfig` and DPE
   definitions between the trial and a fresh scratch org.
6. **Grantmaking is separately licensed and every viewer needs a paid license.** Check:
   license counts before scoping reviewers and portals.
7. **Household naming needs an add-on license.** Check: describe PartyRelationshipGroup
   for `IsExclFrHshldAutoNaming`.
8. **Program Cohorts need Advanced Program Management.** Check:
   `SELECT COUNT() FROM ProgramCohort` succeeds.

## People and households

9. **Person Accounts are effectively required for Fundraising and cannot be turned off.**
   Check: `SELECT IsPersonAccount FROM Account LIMIT 1` compiles.
10. **Person Account security is split:** object access from Account, field-level
    security from Contact fields. Check: for each new person field, confirm the object and
    its field permissions.
11. **A household without its group record is not a household.** Check: household
    business Accounts with no `PartyRelationshipGroup`.
12. **Group Type defaults to Group.** Check:
    `SELECT Type, COUNT(Id) FROM PartyRelationshipGroup GROUP BY Type`.
13. **An inactive membership still rolls up.** DGS ignores `AccountContactRelation.IsActive`;
    remove the relation when someone leaves. Check: count inactive household relations.
14. **One household per person,** or household totals double count. Check: contacts with
    more than one household relation.
15. **No relationship roles ship.** Check:
    `SELECT RoleName, RelatedRoleName, ShouldCreaInversRoleAuto FROM PartyRoleRelation`.
16. **Merging households needs Merge and Split Groups** in a cloned Group Membership set.
    Check: test the merge as the intended user.
17. **Seasonal addresses exist** on ContactPointAddress, despite older partner claims.
    Check: describe ContactPointAddress.

## Gifts and entry

18. **Revenue is on GiftTransaction.** Opportunity reports and Campaign statistics
    undercount. Check: paid transaction totals against Closed Won Opportunity totals.
19. **Status defaults to Unpaid.** Check: `SELECT Status, COUNT(Id) FROM GiftTransaction
    GROUP BY Status` after every load.
20. **Status-dependent required dates.** Check: dry-run a 10-row load with each status.
21. **`OriginalAmount` includes donor-covered fees, excludes processing fees.** Check:
    `OriginalAmount - DonorCoverAmount` on a gateway sample equals the intended gift.
22. **Full refunds need Status Fully Refunded.** Check: transactions whose refunded amount
    equals the original amount but Status is not Fully Refunded.
23. **`IsPaid` and `CurrentAmount` semantics are unclear** (verify). Check: create paid,
    partially refunded and fully refunded rows and read the flags.
24. **Custom Payment Method values broke Gift Entry processing** (known issue, June 2024;
    verify current status). Check: process one gift with the new value in a sandbox.
25. **$0 transactions may be rejected** (partner claim, verify). Check: insert one in scratch.
26. **Only Gift Entry names records.** Check: loaders and integrations set `Name`.
27. **Donor matching follows duplicate rules by default.** Check:
    `FundraisingConfig.DonorMatchingMethod`; run a known duplicate through
    `processGiftEntries` with `isDryRun`.
28. **Processed GiftEntry rows are the audit trail.** Check: group GiftEntry by
    `GiftProcessingStatus`; read `GiftProcessingResult` on failures.
29. **"Partially Processed" batches.** Check: `SELECT Status, FailedGiftCount,
    DoesTotalGiftValueMatch FROM GiftBatch`.
30. **Nothing forces designations to sum to the amount in loads.** Check: sum designation
    amounts per transaction against `OriginalAmount` in a report or Apex.
31. **Exactly one default designation.** Check: `SELECT COUNT() FROM GiftDesignation
    WHERE IsDefault = true` returns 1.
32. **In-kind transactions inflate donor cash totals.** Check: paid In-Kind totals.
33. **Soft credit percents need not total 100.** Check: group soft credits by transaction.
34. **Household auto soft credits duplicate migrated household credits.** Check:
    `SELECT SoftCreditSource, Role, COUNT(Id) FROM GiftSoftCredit GROUP BY
    SoftCreditSource, Role`.

## Commitments

35. **Commitment Status is computed.** Check: the lapsed, failing and auto-close
    settings; status distribution after load against the source.
36. **Only one next transaction is generated.** Check: per active commitment, one future
    Unpaid transaction.
37. **Upgrades create a new schedule.** Check: reports use `CurrentGiftCmtScheduleId`.
38. **Commitment processing errors are silent.** Check:
    `SELECT COUNT() FROM GiftCommitment WHERE LastNextGenCmtProcError != null`.
39. **No twice-monthly schedule.** Check: list source recurring gifts with day 29 to 31
    or a 1st and 15th period before mapping.

## Rollups and reporting

40. **DPE templates do nothing until copied and activated;** program templates are not
    scheduled by default. Check: Setup > Data Processing Engine.
41. **Copies do not auto-update across releases** unless the Manage Fundraising DPE
    Definitions action is used; never customize those auto-updated copies. Check: copy
    names carry the release.
42. **Three identities must be right** (admin, writeback user, Analytics integration
    user). Check: Monitor Workflow Services for Failed or Completed With Failures.
43. **DPE budget is finite** (30 hours, 10 million rows a month, 50 definitions). Check:
    DPE usage against an 80 percent alert.
44. **Overlapping DPE runs fail.** Check: schedule table of DPE flows, 15 minutes apart.
45. **Donor summaries are calendar year.** Check: `GiftsThisYearAmount` against a
    fiscal-year transaction report.
46. **DGS rows are rebuilt each run** (observed 2024, verify). Check: DGS `CreatedDate`
    after a run.
47. **SAQL filters are case-sensitive.** Check: filter nodes use `true`, not `True`.
48. **Only 3 Data Pipelines Base User licenses.** Check: license usage; a custom admin
    group without it.

## Programs and grants

49. **`ProgramEnrollment.IsActive` defaults to false,** so imported enrollees count as
    zero. Check: `SELECT IsActive, COUNT(Id) FROM ProgramEnrollment GROUP BY IsActive`.
50. **No Data Import Wizard, no application-to-award automation, and IndividualApplication
    appears frozen (**verify**).** Check: plan loaders and award automation explicitly.

## Carry-over

- Green job status is not row success: read GiftEntry status, GiftBatch failed counts and
  "Completed With Failures".
- Reconcile migrations by legacy external ID, not by created-today counts; restore every
  bypassed rule afterwards.
- Receipts belong to cleared payments; fair market value goes in `NonTaxDeductibleAmount`.
