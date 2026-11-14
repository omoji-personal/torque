# Documentation and naming

Write the description when you build the component, in the one current source, not in a
later clean-up deploy. State described: Winter '27 (API 68.0), checked 2026-11-14.
`[check]` = testable in the source files. **verify** = not confirmed in a fetched primary
source: verify in the org (an element name: in a retrieved file) or the current
documentation. field-reported = seen in client work, usually one org.

Why it matters: the description shows only in Setup and is the one place the next admin
learns why a component exists. In the field record, descriptions were added afterwards in a
separate deploy of eleven components, one flow needed an extra version only for its
description, a deploy from an older folder stripped them again, and a scan of one sandbox
found more than 40% of active flows with none (field-reported).

## 1. Two texts, two readers

- **Description** is for admins: why the component exists, who uses it, what writes it
  (which flow, integration or load), what depends on it, and the story or ticket.
- **Help text** is for users: what to enter or what the value means, in their words.

"Help Text should say what a field does, while the Description should say how it does it"
[BLOG-DOC].

## 2. What to write, per component

| Component | Where | What to write | Evidence |
| --- | --- | --- | --- |
| Custom field | `description` and `inlineHelpText` in `objects/Object/fields/Field.field-meta.xml` | Description as above. Help text: guidance "that adds value beyond the label". Both on every field, though the API does not enforce them | [SK-FIELD] |
| Custom object, custom metadata type (`__mdt`) | `description` in the object file | What a record is, who creates it, what it links to. About 50 words. Refresh it when fields or rules change | [SK-OBJ] |
| Custom metadata record used as configuration | A Description field on the type, filled on every record | What the record switches or maps, and who may change it | [DG-RT] |
| Validation rule | `description`, `errorMessage`, `errorDisplayField` in the rule file; `/* */` comments in the formula | Description: the business rule and who asked for it. Message: what is wrong and what to do. Comments: why each condition is there | [BLOG-DOC] [SK-VR]; the description is a house standard |
| Formula field | `/* */` comments in the formula, plus the field description | Why the formula is built this way | [BLOG-DOC] |
| Permission set | `label`, `description` | What it grants, for which task | [SK-PS] |
| Permission set group | `description` | Who it is for (which role or persona) | **verify** |
| Custom permission | Its description (**verify** the element in a retrieved file) | What holding it allows or skips | house standard |
| Flow | `description` on the flow and each element and resource | `salesforce-flow/references/build-checklist.md`, step 8 | [DG-RT] |
| Apex class | A `/** */` block before the class and each public or global method | What it does, why it is code, how it is called | [SK-APEX] |
| Record type, report, dashboard | Its description | Who uses it and for what decision | house standard: no Salesforce rule was found. Retrieve one to see the element |
| Lightning web component | A header comment | Why it exists and what it replaces | house standard: no Salesforce rule was confirmed |

Also record the decision behind non-obvious configuration: "Document configuration decisions
and their rationale" [WA-OE].

## 3. Limits

| Text | Limit | Evidence |
| --- | --- | --- |
| Field description | 1,000 characters | [BLOG-DOC] |
| Field help text | 510 characters | **verify** |
| Description on a permission set, a profile or a validation rule | Deploys failed above 255 characters ("cannot be longer than 255", "data value too large"); other types allow more | field-reported, 4 failures in 3 engagements; **verify** the limit per type with a dry run |
| Validation rule error message | 255 characters | [SK-VR] |
| Validation rule name | 40 characters; starts with a letter; letters, digits and underscores; no trailing or double underscore | [SK-VR] |

Run a dry-run deploy to confirm that long texts fit.

## 4. Good and bad

| Text | Bad | Good |
| --- | --- | --- |
| Field description | "Renewal date" (repeats the label) | "Date the membership lapses. Set by the flow Membership_BeforeSave_SetRenewal from Start Date plus Term. Read by the renewal reminder report. Story MEM-41." |
| Field help text | "Enter the renewal date" | "Filled in automatically. Change it only to record an agreed extension." |
| Object description | "Custom object" | "One row per site visit by a field officer. Created from the Visit action on Account. Child of Account; drives the Visits This Year count." |
| Validation rule description | "Blocks save" | "Finance rule: a pledge over the approval limit needs an approver before it is marked Received. Limit is in custom metadata Approval_Limit. Bypass: custom permission Bypass_Pledge_Rules, for data loads only." |
| Validation rule message | "Invalid value" | "Add an approver before you mark a pledge over the approval limit as Received. [VR07]" |
| Permission set description | "Access" | "Lets membership staff read and edit Membership records and run the renewal flow. Assign with Membership_Staff group." |
| Flow description | "Case flow v3" | See the flow skill |

A short code in the error message, such as `[VR07]`, lets an admin find the rule from a
user's screenshot (community convention [SFXD]).

## 5. What never goes in a description or help text

- A version number: it is wrong after the next version (field-reported: a flow description
  said version 17 while version 22 was live).
- The label or API name restated (**verify**: Salesforce Admins advice).
- A person's name. Use the role or team.
- A record Id, a host name, a password or any secret.
- Internal notes in help text: every user of the field can read it.
- "TODO", "temp" or "test" on something that ships.

## 6. Naming

Salesforce states the principle, not one scheme: "Establish a consistent naming convention
for all elements and variables" [DG-RT].

1. Find the org's convention: read the names of twenty existing custom fields, flows,
   validation rules and permission sets on the objects you touch. Write it in the client's
   notes.
2. Follow it, even where you would choose differently.
3. Where there is none, propose one, get a yes, and apply it to everything you build.

| Component | Rules and options | Evidence |
| --- | --- | --- |
| Field | API name starts with a letter and follows the label (each word capitalised, spaces to underscores, `__c`). Label in Title Case. Avoid reserved words such as `Order` and `Group`. Relationship names plural | [SK-FIELD] |
| Field, optional scheme | A prefix or suffix by purpose, for example `Is` for checkboxes or a marker for hidden technical fields | community [SFXD] |
| Validation rule | The limits in section 3. Option: a numbered code, object and what it prevents, with the code repeated in the message | [SK-VR]; community [SFXD] |
| Permission set | Name the task, not the job title. Build groups per persona. A prefix by kind keeps them sorted in Setup | **verify** |
| Flow | A pattern such as `Object_FlowType_Action`. Short API names | **verify** |
| Flow elements | Verb phrases for actions, questions for decisions; a short code per element helps find it from an error email | community; **verify** |
| Apex | Classes in PascalCase, methods in camelCase starting with a verb, constants in upper snake case | [SK-APEX] |

- Give one build a unique prefix where the org's convention allows, and list anything that
  exists only in the sandbox, so a reset or a removal can target exactly what was built
  (field-reported).
- No default or copied names ("Copy of", "New Flow", "Field1").

## 7. Check it

- `[check]` every changed object, field, validation rule, permission set and flow file has a
  non-empty `description`; every field a user fills in has `inlineHelpText`; no description
  equals the label; validation rule names and messages are within the limits.
- Flows and their elements: `sf code-analyzer run --rule-selector flow --target PATH` (rule
  `MissingDescription`, **verify**).
- Custom fields without a description across a project: `sf hardis:lint:missingattributes`
  if the community sfdx-hardis plugin is already installed (run it as an external command)
  [HARDIS].
- In the org after deploy: read `Description` on `FlowDefinitionView` for the flows you
  touched (field-reported); open one changed field in Object Manager.
- If a script generates metadata, keep descriptions as data and re-apply them on every run
  (field-reported).

## 8. The data dictionary

For hand-over, generate a dictionary from the metadata, not by hand: object, field, type,
description, help text, classification, what writes it. Blank descriptions show up as blank
rows, which is the point. See `salesforce-delivery/references/handover-and-support.md`.

## Sources

- [BLOG-DOC] Salesforce blog, how to document inside your Salesforce instance (2019): https://www.salesforce.com/blog/how-to-document-inside-your-salesforce-instance/
- [SK-FIELD] https://github.com/forcedotcom/sf-skills/blob/main/skills/platform-custom-field-generate/SKILL.md
- [SK-OBJ] https://github.com/forcedotcom/sf-skills/blob/main/skills/platform-custom-object-generate/SKILL.md
- [SK-VR] https://github.com/forcedotcom/sf-skills/blob/main/skills/platform-validation-rule-generate/SKILL.md
- [SK-PS] https://github.com/forcedotcom/sf-skills/blob/main/skills/platform-permission-set-generate/SKILL.md
- [SK-APEX] https://github.com/forcedotcom/sf-skills/blob/main/skills/platform-apex-generate/SKILL.md
- [DG-RT] https://architect.salesforce.com/docs/architect/decision-guides/guide/record-triggered
- [WA-OE] https://architect.salesforce.com/docs/architect/well-architected/guide/operational-excellence.html
- [SFXD] Community conventions (2018): https://sfxd.github.io/article-convfields.html and https://sfxd.github.io/article-convvr.html
- [HARDIS] sfdx-hardis (community, AGPL-3.0): https://sfdx-hardis.cloudity.com/hardis/lint/missingattributes/
- **verify** items: help text limit (https://help.salesforce.com/s/articleView?id=sf.customhelp_defining_field_level_help.htm&type=5),
  permission set naming (https://help.salesforce.com/s/articleView?id=sf.perm_sets_best_practices.htm&type=5),
  flow naming (https://admin.salesforce.com/blog/2025/planning-for-flow-success-building-automation-that-scales),
  documentation advice (https://admin.salesforce.com/blog/2021/documentation-advice-from-the-awesome-admin-community).
- field-reported: lessons from real client work with Torque and its predecessor, not re-tested for this release.
