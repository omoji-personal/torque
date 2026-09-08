---
description: "Compare two orgs or an org and baseline to explain meaningful differences."
---

# /compare-orgs

Compare two orgs or an org and baseline to explain meaningful differences.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Resolve the two named environments independently, including org IDs, namespaces,
package/API versions and access. The user or client configuration chooses the baseline;
there is no global canonical org. Keep source and destination visible in each result.

Compare only relevant settings/custom metadata, schema, active Flow definitions and
versions, automation, permission configurations/assignments, layouts and integration
configuration. Exclude secrets and org-specific identifiers from generic semantic diffs;
retain private identifiers when needed to interpret a finding. Separate unavailable
metadata and truncated results from actual differences.

For “works here but not there,” correlate differences with the concrete user, record
type, entry conditions and error. Raw XML ordering, API versions, generated identifiers
and namespace differences may be noise; explain normalization rather than hiding it.
Use `/retrieve-current` for fresh snapshots and `/advisory flow` for activation evidence.

Save a comparison with dates, scope, meaningful matches/differences, hypotheses and
next steps. Comparison alone does not synchronize either org. If synchronization is
requested, prepare and carry out only the intended changes through normal tools.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
