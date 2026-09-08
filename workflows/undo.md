# /undo

Reverse the specified recent work using actual change evidence and available recovery paths.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Determine exactly which change the user means from task history, session records,
diffs, deployment jobs and snapshot manifests. A specific “undo that update” is already
an instruction to reverse that change; ask only if multiple plausible targets remain.

Inventory local files, org metadata, settings, records and activation state separately.
Identify each inverse operation and available exact pre-state. Preserve unrelated
dirty files and later edits; do not assume HEAD~1 is this session's baseline, or use
a blanket reset/checkout. For metadata, account for intervening org drift. For updated
records, use captured original values. Created records may have dependencies; deleted
records may or may not be recoverable depending on actual deletion state and retention.

For supported bundles use `/revert-preview` and `/revert`. Otherwise execute a narrowly
scoped inverse through ordinary Salesforce/local tools within the user's request.
Do not turn an unsupported automatic restore into a fake success or run a broad
destructive operation because it resembles an undo. Explain specific unrecoverable
parts and complete independent reversible parts where useful.

Read back the restored state and representative outcome; record actual success,
partial failure, remaining drift and side effects. Undo is not a generic time machine:
restored access metadata does not reverse data edited while that access existed.
