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

Metadata writes through `torque deploy --metadata Type:Name` can produce supported
bundles when before/after capture is complete. Manifest-only, source-directory and
dry-run deploys do not capture complete before-state. `torque data` records capture
operation-specific evidence; bulk update/upsert restores use a reviewed `replay.csv`
and exact job IDs, not a blanket replay of forensic CSVs. A `/retrieve-current`
baseline supports manual recovery and is not itself an automatic recovery bundle.

For supported bundles use `/revert-preview` and `/revert`. Otherwise execute a narrowly
scoped inverse through ordinary Salesforce/local tools within the user's request.
Do not turn an unsupported automatic restore into a fake success or run a broad
destructive operation because it resembles an undo. Explain specific unrecoverable
parts and complete independent reversible parts where useful.

To remove records the work created, delete only the Ids saved when they were created
or taken from the exact job's successful results (an Id-file delete such as
`sf data delete bulk --file ids.csv` with an Id column). Never build a delete from a
query that matches by name, title, creator or date. If the Id list is lost or
incomplete, stop and ask instead of matching.

Read back the restored state and representative outcome; record actual success,
partial failure, remaining drift and side effects. Undo is not a generic time machine:
restored access metadata does not reverse data edited while that access existed.
