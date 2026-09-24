---
description: "Design a record-triggered flow that keeps NPSP payments and gift batches in sync."
---

# /gift-payments

Design automation that keeps a payment's processed date in step with its gift batch.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Confirm the actual request before designing: which payment field should be set, what
"batch closes" means in the client's org (the NPSP Batch Gift Entry template and its
default payment status), and whether existing unpaid or partially processed payments
need a one-time backfill separate from the ongoing automation.

1. Design a record-triggered flow on `npe01__OppPayment__c` (NPSP payments), running
   after save. Entry criteria: `npe01__Paid__c` equals True and
   `npe01__Payment_Date__c` is null. This targets a payment the batch just marked
   paid, not one already dated.
2. Set `npe01__Payment_Date__c` to the appropriate date: today, or the batch's own
   transaction date if the org's batch template exposes one. Proposal, not verified
   behavior: confirm whether the client's Batch Gift Entry template sets
   `npe01__Paid__c` to True directly, since that assumption drives the entry
   criteria; if the template instead leaves payments unpaid until a separate step,
   the trigger condition changes.
3. Bulk-safety: the flow must evaluate the whole triggering record collection, not
   one record at a time, with no SOQL or DML inside a loop. Confirm the entry
   criteria and update stay inside Salesforce's per-transaction limits for a full
   batch-gift close, which can produce hundreds of payments in one transaction.
4. Test plan, three cases: (a) a single payment marked Paid with no processed date,
   confirm the date is set and no duplicate update fires on a later unrelated save;
   (b) a bulk batch close producing 200+ payments in one transaction, confirm every
   payment is dated and the transaction stays inside limits; (c) a payment marked
   Paid that already has a processed date, confirm the flow leaves it unchanged.
5. Rollback: deactivate the flow version. Existing dated payments are unaffected
   since the flow only ever sets a null field. Document whether any payments were
   backfilled outside the flow, since that data change does not revert with
   deactivation.

Mark the batch-template assumption in step 2 as a proposal until confirmed against
the client's actual configuration. Save the design and open questions to the client
session.

## In build-only mode

When the workspace's `ai_access` is `build-only`, the agent cannot reach an org or read
`clients/`. It works only from material the consultant supplies with names, IDs and values
removed, for example the redacted batch template settings or payment field list. Every step above that needs the live org, a
record or the client session becomes an explicit hand-off: the agent says what the
consultant should check, run or record, and marks each conclusion that depends on it as
unconfirmed. Here, confirming the batch template's behaviour, running the test plan in an
org, and saving the design to the client session are hand-offs.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
