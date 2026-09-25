# Program Management Module and Outbound Funds

Both are free managed packages that sit beside NPSP. Neither feeds NPSP donor rollups.
Confirm namespaces, versions and API names with describe before building.

## PMM (namespace `pmdm`)

PMM uses standard Contact and Account and does not require NPSP. With NPSP, household
naming and address automation still act on the shared Contacts and Accounts.

A Program is the offering. Two paths meet at attendance: people join a Program through a
Program Engagement; a Program offers Services, which run on Service Schedules that
generate Service Sessions.

| Object | Meaning | Notes |
| --- | --- | --- |
| Program (`pmdm__Program__c`) | The ongoing offering | Parent of engagements, services, cohorts |
| Program Cohort (`pmdm__ProgramCohort__c`) | Optional intake group | Engagements can attach to one |
| Program Engagement (`pmdm__ProgramEngagement__c`) | A person's enrollment in a program | Contact plus Program; Stage, Role, start and end dates. One per person per program unless the client wants one per year |
| Service (`pmdm__Service__c`) | What the program delivers | Unit of measure that Quantity counts |
| Service Schedule (`pmdm__ServiceSchedule__c`) | Recurring pattern | Generates sessions; holds the participant list |
| Service Session (`pmdm__ServiceSession__c`) | One dated occurrence | Status is a restricted picklist: Pending, Complete, Canceled. The lookup to its schedule is optional |
| Service Participant (`pmdm__ServiceParticipant__c`) | Standing sign-up to a schedule | Source of the attendance roster. Populate `pmdm__Contact__c` yourself; it is not derived from the engagement |
| Service Delivery (`pmdm__ServiceDelivery__c`) | One delivery, the attendance record | Contact, Service, Program Engagement, Service Session, delivery date, quantity, attendance status (Present, Excused Absence, Unexcused Absence; confirm the values) |

Keep the grains apart: engagement per program, participant per schedule, delivery per
session. A one-time drop-in needs a delivery (and an engagement), not a participant; a
participant puts the person on every future roster.

### Attendance surfaces

- Track Attendance on a Service Session loads its participants and creates or updates
  Service Deliveries for the roster. Columns come from the `Attendance Service
  Deliveries` field set; the schedule wizard's participant columns come from the
  `SessionParticipantView` field set on Program Engagement.
- Bulk Service Deliveries enters deliveries row by row and can create a missing Program
  Engagement inline. Nothing native creates Contacts or applies a per-year engagement rule.
- If a custom process writes deliveries only for people present, absences are never
  recorded and attendance rate reads 100 percent. Decide with the client whether an
  absence is a record.
- A custom attendance process should enforce one delivery per contact per session, for
  example with a unique text field holding Contact Id and Session Id.

### Attendance rollups and gates

- PMM ships attendance rollups (attendance summary, attendance rate, number of present
  and absent deliveries, for example `pmdm__NumPresentServiceDeliveries__c`) on Contact,
  Program Engagement, Service, Service Session and Service Schedule. They have no year
  bound.
- Incremental rollup triggers are switched on by records of the PMM feature gate custom
  metadata type; they were off in the installed version observed. Scheduled Apex
  recalculates and backfills. Turn gates on by editing the managed records in Setup.
  Never deploy your own copies of those records: a duplicate DeveloperName made the PMM
  delivery trigger fail on every insert until a destructive deploy removed it.
- A count per person per program per year belongs on the engagement (if engagements are
  annual) or in reports grouped by year. A stored "this year" rollup does not reset on
  January 1 without a scheduled full recalculation.

### Access

PMM ships three permission sets: `PMM : Manage`, `PMM : Deliver`, `PMM : View` (API names PMDM_Manage, PMDM_Deliver, PMDM_View). Program and
case data is often sensitive; review sharing and field-level security by team.

Checks:

- `SELECT pmdm__Status__c, COUNT(Id) FROM pmdm__ServiceSession__c GROUP BY pmdm__Status__c`
  (confirm field names with describe).
- Participants with no Contact: `SELECT COUNT() FROM pmdm__ServiceParticipant__c WHERE
  pmdm__Contact__c = null`.
- Duplicate deliveries: group `pmdm__ServiceDelivery__c` by contact and session with
  `HAVING COUNT(Id) > 1`.

## Outbound Funds (namespace `outfunds`)

Funder-side grants: Funding Program (`outfunds__Funding_Program__c`), Funding Request
(`outfunds__Funding_Request__c`), Requirement, Disbursement (`outfunds__Disbursement__c`),
Review. Rollup and system fields such as amount disbursed are package-computed and not
writable. An optional NPSP extension adds GAU Expenditure, linking disbursements to GAUs;
expenditures are outflows, not allocations.

Automation that creates disbursements on approval should key on the status transition,
not the status value, so a re-save does not duplicate the award.

## Sources

- PMM overview: https://help.salesforce.com/s/articleView?id=sfdo.pmm_overview.htm&type=5
- PMM engagements and cohorts: https://trailhead.salesforce.com/content/learn/modules/service-delivery-with-program-management-module-pmm/manage-program-engagements-and-program-cohorts
- PMM source: https://github.com/SalesforceFoundation/PMM
- Outbound Funds data model: https://help.salesforce.com/s/articleView?id=sfdo.OFM_Get_to_Know_the_Data_Model.htm&type=5
- Outbound Funds wiki: https://github.com/SalesforceFoundation/OutboundFundsModule/wiki
