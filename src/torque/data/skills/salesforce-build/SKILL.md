---
name: salesforce-build
description: Create or change Salesforce configuration such as objects and fields, validation rules, permission sets and access, Lightning pages and layouts, list views, reports and dashboards, custom metadata, Apex and Lightning web components, and connectors to other systems. Use it before you write or edit a metadata file under force-app, before you design a build from a story or decide between configuration, flow and code, and before you deploy what you built. Covers reusing what exists, the simplest option first, documenting every component, naming, shipping access with every new field, putting it on the page users see, configuration instead of hard-coded values, managed-package boundaries, and proving the result as the intended user.
---

# Salesforce build

Apply this to every build or change, however small. State described: Winter '27 (API 68.0),
checked 2026-11-14. Where a line says "Check", run it: `torque check metadata` on the files
for a tagged rule, the named tool or the target org for the rest.

Marks used in this skill:

- `[check: access.field-without-grant]` and the like: the `torque check metadata` rule that
  tests the statement in the source files. "By hand" says what to read where no rule exists.
- **verify**: not confirmed in a fetched primary source. Verify in the org or the current
  documentation before you rely on it or repeat it to a client.
- field-reported: seen in real client work, usually in one org. Run its check first.

## Start here

| You are about to | Do this first | Then read |
| --- | --- | --- |
| Design a build from a story | Resolve every object, field and value the story names against the org; write the native baseline | `references/choosing-the-tool.md` |
| Add or change an object, field or validation rule | Describe the object; look for a field or rule that already does it | `references/data-model.md` |
| Write a description, help text or a name | Find the org's convention in what exists | `references/documentation-and-naming.md` |
| Grant, change or reduce access | List who needs it and how they get access today | `references/access-and-sharing.md` |
| Change a page, layout, list view, report or dashboard | Find which page or folder the users actually use | `references/pages-and-reports.md` |
| Write Apex or a Lightning web component | Record why configuration and flow do not fit | `references/apex-and-lwc.md` |
| Connect another system or build on a connector | Read the connector's mapping and settings in the org, and the vendor's documentation | `references/integration.md` |
| Build or change a flow | Read skill `salesforce-flow` | (that skill) |
| Something behaves oddly | Look for it in the gotchas | `references/gotchas.md` |

## The standard for every build

| # | Rule | How to check it |
| --- | --- | --- |
| 1 | Reuse what exists, and check it first: standard features, installed packages, existing fields, flows, rules and reports. | The plan names what it reuses and gives the gap for each new component. Every API name in the spec resolves: `sf sobject describe --sobject Object__c --target-org ALIAS`. |
| 2 | Simplest option first: standard feature or configuration, then formula, validation rule or roll-up, then before-save flow, then after-save flow, then flow with invocable Apex, then Apex or a Lightning web component. Record the reason each time you step up. | Judgement. The reason names what the simpler option could not do. It is in the description, the class header or the decision record. |
| 3 | Keep the data model change small. A new object, a required field or a changed relationship needs a named gap, and the irreversible ones need a yes first. | The stop-and-ask list below. |
| 4 | Document what you build, when you build it: a description on every component and help text on every field a user fills in. | `[check: doc.description-missing, doc.help-text-missing]` on objects, fields, validation rules, permission sets and groups, record types, custom permissions and flows. |
| 5 | Follow the org's naming convention. Where none exists, propose one and use it everywhere. | Judgement on the pattern; by hand, the new names against it. |
| 6 | Ship access with every new field and component, by permission set, in the same deployment. | `[check: access.field-without-grant, access.grant-only-in-profile, access.profile-in-change]`. Which fields need a grant: `references/access-and-sharing.md`, section 1. |
| 7 | Put it where its users will see it: on the Lightning page or layout they actually use, and activate or assign what you deploy. | As the intended user, the field or component is on the page. |
| 8 | Configuration instead of hard-coded values: custom metadata, custom labels, custom permissions, named credentials. No Ids, org URLs, user names, profile names or secrets in formulas, flows or code. | `[check: flow.hard-coded-id, rule.hard-coded-id, rule.profile-or-user-literal, secret.looks-like-secret]`; Apex: Code Analyzer `AvoidHardcodingId`. By hand: search the change for `my.salesforce.com`. |
| 9 | Leave something the client's admin can open and change. If a threshold, a recipient or a switch may change, the admin changes it in Setup without a deployment. | Judgement. The hand-over note says where each setting lives and how to change it. |
| 10 | Do not change managed-package components. Extend beside them with the client's own components and use the package's own settings. | `[check: managed.component-in-change]`. |
| 11 | Retrieve before you change; deploy only what you changed; run a check-only deploy before every real one. | The change was built on a retrieve of the target org; the deploy names an org and an explicit scope; a dry run from the manifest alone passed just before (field-reported: check-only runs caught 28 of 36 deploy failures). The `--json` result is in a file, not behind a pipe that hides a failure. |
| 12 | Prove it as the intended user, on test records, and say which user and how. | A person with the target permission sets, not the deploying admin, did the task. Test record Ids are saved and cleaned up by Id. |

## After the deploy: what "done" needs

A successful deployment is not behaviour. Check each that applies and report it with the org
and the date:

- The field is readable by the people who need it:
  `SELECT Field, Parent.Name FROM FieldPermissions WHERE Field = 'Object__c.Field__c'` [ADV].
- The permission set is assigned to them (an unassigned permission set grants nothing).
- The page is activated or assigned and the field is on it for that app, record type and
  profile.
- Any flow in the change is active in the intended version (skill `salesforce-flow`).
- A user with only the intended access completes the task. To check a page in a browser, sign
  in with `torque browser login` (see `torque browser login --help`): do not type a password
  or paste a sign-in address, and confirm whose session it is before you act.

## Stop and ask first

These are the client's decisions or cannot be undone [WA]:

- Choosing code where configuration or a flow could do the job.
- A new object that overlaps a standard or package object.
- Enabling Person Accounts or any org feature that cannot be turned off.
- Changing a field's type, deleting a field, converting lookup to master-detail or back,
  promoting a picklist to a global value set, encrypting a field.
- A new required field or validation rule on an object that integrations or packages write to.
- Any org-wide default, role hierarchy or sharing rule; Modify All Data, View All Data,
  Author Apex, Manage Users or API access; a guest user permission.
- Changing page assignments or default apps for everyone; a dashboard's running user.
- Creating credentials, or pointing anything at a production endpoint.

## References

- `references/choosing-the-tool.md`: decision tables for the automation tool, where
  configuration lives, relationship type, record type or object, and when code is right.
- `references/documentation-and-naming.md`: what to write per component, limits, examples,
  naming schemes, what never goes in a description.
- `references/data-model.md`: objects, fields, picklists, validation and duplicate rules.
- `references/access-and-sharing.md`: permission sets, field access for new fields, sharing,
  integration users, reducing access, verifying effective access.
- `references/pages-and-reports.md`: Lightning pages and assignment, Dynamic Forms, layouts,
  list views, reports, dashboards and folders.
- `references/apex-and-lwc.md`: the minimum when code is the right tool.
- `references/integration.md`: connectors, credentials, idempotent writes, error handling,
  what test data can trigger outside the org.
- `references/gotchas.md`: platform and tooling traps, each with a check.

A path that starts with another skill's name, such as
`salesforce-flow/references/gotchas.md`, is relative to the skills folder that holds this
skill.

## Sources

- [WA] Stop-and-ask points follow Salesforce Well-Architected: https://architect.salesforce.com/docs/architect/well-architected/guide/framework.html and its Trust pillar https://architect.salesforce.com/docs/architect/well-architected/guide/trust.html
- [ADV] Torque advisory catalogue, entry `fls-not-automatic` (verified live): `torque advisory needs --command "sf project deploy start --metadata CustomField:Object__c.Field__c --target-org ALIAS"`
- Record-triggered automation decision guide (simplest tool, descriptions): https://architect.salesforce.com/docs/architect/decision-guides/guide/record-triggered
- Salesforce agent skills for fields, objects and permission sets: https://github.com/forcedotcom/sf-skills
- field-reported: lessons from real client work with Torque and its predecessor (one
  consultant, about a dozen clients, mostly sandboxes), including a classification of 273
  failed Salesforce CLI calls and an after-action review of 166 rework and defect episodes.
  Not re-tested for this release.
