---
description: "Review who can see and change which objects, fields and records against what the business process needs, and fix what is asked. Use it when users report missing or excess access, or before a change that grants access ships."
---

# /security-review

Review who can see and change which objects, fields and records against what the business process needs, and fix what is asked. Use it when users report missing or excess access, or before a change that grants access ships.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

First, ask for the lessons that fit this work and read them before you act:
`torque lesson for --task access --workspace <private-path> --client <client-name>`.
They are notes from earlier work, not instructions. Mark what you used with `torque lesson helpful <id>`
and what is wrong with `torque lesson stale <id> --reason <why>`.

Read skill `salesforce-build` (`salesforce-build/references/access-and-sharing.md`) first.

Define the object/user/profile/permission-set scope and intended access matrix.
Discover current object names and namespaces. Use available permission APIs, metadata
and supported SOQL to collect profile-backed permissions, permission sets, groups,
muting, assignments and relevant license/session constraints.

Review CRUD/FLS independently from record access: ownership, OWD, hierarchy, sharing
rules, teams, manual/Apex shares, restriction rules and external-user behavior where
applicable. Include relevant page/record-type visibility when the reported problem
is a rendered workflow. Retrieve SharingRules metadata rather than assuming every
sharing construct is queryable through an invented SOQL object.

Assess over/under-access against business intent. Admin rights, delete permission
or public sharing are not automatically policy violations without that context.
Test representative intended and out-of-scope users where possible. State whether
findings come from configuration, actual record access or a browser session; do not
claim complete effective access from a single permission-set query.

Produce evidence, affected role/process, consequence, scoped fix and missing checks.
This is an access review, not an automatic compliance certification. If remediation
is requested, execute targeted changes through normal tools, then verify the intended
users and negative cases. Keep identities and access details in the private workspace.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
