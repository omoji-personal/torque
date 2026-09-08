# /snapshot

Capture a scoped point-in-time baseline, distinguishing reference evidence from recovery payloads.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Identify target, purpose and exact scope. For configuration analysis, capture relevant
metadata/settings, active automation, permission configuration, versions and selected
counts. For reversibility, preserve the exact component source or record pre-values
required by the intended inverse operation. A Markdown summary/count is not a backup.

Use normal Salesforce retrieval/query tools or the optional snapshot-aware execution
delegate when its operation is supported. `torque revert --help` lists that delegate;
`/revert-show` lists captured operation bundles. This guided `/snapshot` recipe does
not imply a universal native snapshot command exists.

Store identity, capture time, selectors, completeness, original artifact paths and
checksums with the baseline. Keep precise sensitive recovery values in private
recovery artifacts; summaries can be redacted. If redaction removes original values,
do not mark that redacted summary reversible.

Validate that the needed files/results actually exist and identify missing surfaces.
For ongoing work keep the baseline immutable and stage proposed edits separately.
Report what the capture can restore, what it merely documents, and operations needing
manual recovery or external backups. Use `/revert-preview` before a later restoration.
