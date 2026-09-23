# /triage-alert

Turn a Salesforce flow or Apex error notification into cause, fix and verification.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Start from the exact notification: object, flow or Apex class name, element or line,
error code, message and the triggering record. A forwarded email or screenshot is a
starting point, not the whole evidence; confirm the live version still matches it.

1. Identify the flow (or class) and the active version that actually ran. Flow fault
   notifications report the version that failed; confirm it is still the active one
   before diagnosing a version nobody runs anymore.
2. Read the element that failed: its type, the raw error code and message, the
   triggering record, and the interview/input variable values at that point.
   `REQUIRED_FIELD_MISSING` names the field; confirm which object it lives on and
   whether the flow or an upstream process was expected to populate it.
3. Classify the likely path: data (a record missing a value the flow assumed
   present), configuration (entry criteria, a formula, a changed picklist, record
   type or page layout), bulk (works for one record, fails past roughly 200 records
   or a bulk API load), or permission (the running user or context user lacks
   CRUD/FLS on the object or field).
4. State the likely cause and the evidence for it, separate from any remaining
   hypothesis. Note whether the same fault could recur for other records right now.
5. Propose the narrowest fix: a default value, a corrected entry condition or fault
   path, a bulkified element, or a scoped permission-set grant. Do not widen access,
   relax validation, or add a blanket fault handler as a substitute for the actual
   cause.
6. State how to verify: which record(s) to retest, whether a bulk case is needed,
   and what a passing run and a clean interview or debug log look like.
7. State what to tell the client: plain-language cause, scope (how many records or
   users affected, over what period), the fix, and any past records the fix does not
   retroactively repair.

Report observed cause versus remaining hypotheses; do not describe a fix as verified
before it has actually been retested. Save the outcome to the client session.
