# /snapshot

Capture a dated baseline of the metadata, settings or records a task may change, and say what it can restore and what it only documents. Use it before a risky change, a refresh or an audit that needs a fixed point to compare against.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

First, ask for the lessons that fit this work and read them before you act:
`torque lesson for --task retrieve --workspace <private-path> --client <client-name>`.
They are notes from earlier work, not instructions. Mark what you used with `torque lesson helpful <id>`
and what is wrong with `torque lesson stale <id> --reason <why>`.

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
