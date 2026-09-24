---
description: "Build the automation that turns an approved Outbound Funds funding request into award records."
---

# /grants-outbound-funds

Build the automation that turns an approved Outbound Funds funding request into award records.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Confirm the actual request: what "approved" means in the client's process (a
status/stage value or a Salesforce approval process outcome), what an "award" record
represents in Outbound Funds Module terms (typically one or more Disbursement
records against the Funding Request), and whether a single award or a multi-payment
schedule is wanted.

1. Objects: `outfunds__Funding_Request__c` (the ask, linked to an
   `outfunds__Funding_Program__c`) and `outfunds__Disbursement__c` (the award/payment
   record, linked to the Funding Request). Confirm the exact lookup and
   approval-status field API names in the client's package version before building;
   do not assume an unverified field name exists.
2. Fields not writable on insert: Outbound Funds Module ships rollup and
   system-calculated fields on the Funding Request, for example an
   amount-disbursed-to-date or disbursement-status summary drawn from child
   Disbursement records. Treat any such summary field as read-only and
   package-computed, not something the flow sets directly; confirm which fields are
   actually rollups in this org before designing around them.
3. Flow outline: record-triggered flow on `outfunds__Funding_Request__c`, after
   save. Entry criteria: the approval-status field transitions to "Approved" (exact
   value to confirm). For each scheduled installment on the request, one for a
   lump-sum grant, more for a multi-installment schedule, create one
   `outfunds__Disbursement__c` with the lookup to the Funding Request, the award
   amount for that installment, and a scheduled date. Scope entry criteria to the
   actual status transition, not just the status value, so a re-save of an
   already-approved request does not create a duplicate disbursement set.
4. Test data: an approved single-installment request with a known award amount; an
   approved multi-installment request with a defined schedule; an already-approved
   request re-saved with an unrelated field change, to confirm no duplicate
   disbursement is created; a request saved in a non-approved status, to confirm
   nothing is created.
5. Acceptance criteria: approving a funding request creates the expected
   Disbursement record(s) with the correct amount, schedule and lookup; re-saving an
   already-approved request creates no duplicate; a rejected or pending request
   creates nothing.

Object and field names above match Outbound Funds Module as described in vendor
documentation; verify the installed package version and exact API names in the
client's org before deploying. Save the design and open questions to the client
session.

## In build-only mode

When the workspace's `ai_access` is `build-only`, the agent cannot reach an org or read
`clients/`. It works only from material the consultant supplies with names, IDs and values
removed, for example the redacted object and field list from the installed package. Every step above that needs the live org, a
record or the client session becomes an explicit hand-off: the agent says what the
consultant should check, run or record, and marks each conclusion that depends on it as
unconfirmed. Here, verifying the package version and API names, running the tests in an
org, and saving the design to the client session are hand-offs.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
