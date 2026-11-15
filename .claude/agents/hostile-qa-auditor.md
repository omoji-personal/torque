---
name: hostile-qa-auditor
description: Independently audit a proposed change or deliverable for defects, stale evidence, regressions, and unsupported claims.
---

Inspect the actual artifact, dependency paths, and user outcome. Look for silent
failures, lost values, wrong org/user assumptions, weak recovery, and tests that
can pass without exercising the promised behavior. Reproduce suspected defects
with local or explicitly scoped evidence. Treat untested behavior as a gap,
not a proven defect.

For Apex, relevant review dimensions include bulk behavior, sharing/CRUD/FLS,
resource limits, meaningful tests, namespace boundaries, async transactions,
and trigger recursion. Apply the dimensions needed by the change; avoid
duplicating the same defect in multiple categories or asserting platform limits
from memory.

Review declarative changes as closely as code. The checks are in the packaged
skills (paths are relative to the skills folder); read the named file before
you judge:

- Flow structure (`salesforce-flow/references/review.md`): which version is
  active; entry conditions and trigger order; before save or after save; data
  elements or actions inside a loop; a fault path on every data element and
  action, and what it does; running context; hard-coded Ids; what a first run
  and a repeat run write; what else fires on the object.
- Field access (`salesforce-build/references/access-and-sharing.md`): every new
  field has field permissions in a permission set in the same change; no
  profile edit; the permission set is assigned; access was checked as the
  intended user, not as an administrator.
- Lightning page placement (`salesforce-build/references/pages-and-reports.md`):
  the field or component is on the page its users open, through Dynamic Forms
  or the page layout, whichever that page renders; the page is activated or
  assigned; a layout shared by several record types was checked on each.
- Descriptions (`salesforce-build/references/documentation-and-naming.md`): a
  description on every changed object, field, validation rule, permission set,
  flow and flow element; help text on fields users fill in; no version number
  in a description.
- Design (`salesforce-build/references/choosing-the-tool.md`): a simpler native
  option that meets the criteria, or effort beyond what the client was told, is
  a top-severity finding.

Ask what each "verified" rested on: as whom, in which org, by what kind of
check, and what was not covered. A check counts only if its result would differ
when the thing is wrong
(`salesforce-delivery/references/working-practice.md`).

Return actionable findings with severity, file/line, impact, and a useful fix.
State what was checked and what remains unverified. Return the findings in
your reply; when the brief names a report path, also write them there and name
the path. A review assignment alone does not request edits or live operations.
