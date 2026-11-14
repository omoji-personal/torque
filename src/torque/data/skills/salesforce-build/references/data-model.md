# Data model: objects, fields, picklists, validation and duplicate rules

Use this before you add or change an object, a field or a rule on data. State described:
Winter '27 (API 68.0), checked 2026-11-14. A tag such as `[check: doc.description-missing]`
names the `torque check metadata` rule that tests the statement.
**verify** = not confirmed in a fetched primary source: verify in the org or the current
documentation. field-reported = seen in client work, usually one org. Queries marked "lead"
were not run for this release: confirm names with describe and treat an error as "not
checked", not as "none".

## 1. Before you add anything

1. Describe the object in the target org: `sf sobject describe --sobject Object__c
   --target-org ALIAS --json`. Names in a spec, a story or the repository are not the org:
   resolve each object, field API name, record type and picklist value the work names
   (field-reported: a rule named a stage that did not exist, a spec was one word off a real
   API name, and two sandboxes had different names for the same field).
2. Look for what already does the job: a standard object or field, an installed package's
   object (`sf package installed list --target-org ALIAS`), an existing custom field. Custom
   objects are for what is specific to the organisation [TH-DM].
3. Keep the change small: prefer a field to an object and an existing object to a new one
   (field-reported preference: "perhaps only one new object"). Trace every new field to a
   requirement line, or ask (field-reported: a field built from an analysis column,
   questioned on day one, was removed in UAT with four deploys).
4. Check the object's headroom: Setup > Object Manager > the object > Object Limits.

Check: the plan lists what is reused, and gives the named gap for each new object or field.

## 2. A new object

| Decide | Default and rule | Evidence |
| --- | --- | --- |
| Sharing model | Ask. The generating skill defaults to `ReadWrite`, or `ControlledByParent` under a master-detail; Salesforce's guidance is Private for sensitive data | [SK-OBJ] [WA-T] |
| Relationship to its parent | `choosing-the-tool.md`, section 6 | |
| Description | Required by this standard | `documentation-and-naming.md` |
| Access | Object and field permissions in a permission set, in the same deploy | `access-and-sharing.md` |
| Where users reach it | Tab, page, related list on the parent | `pages-and-reports.md` |
| Keys for loads and integrations | An external Id field, unique, so writes can be upserts | [DG-INT] |

Check: `[check: doc.description-missing]`; by hand, `sharingModel` in the object file and the
object's grant in a permission set in the change.

## 3. Fields

- **Required is a cost.** A required field blocks every write path that lacks the value,
  integrations and packages included, and shuts out people who do not have the data. Record
  why each required field is mandatory [WA-F]. Required on the page layout binds only the
  page; required on the field binds the API too [ADV `layout-required-is-not-api-required`].
- **Classify when you create.** Public, Internal, Confidential or Restricted, recorded on
  the field, drives field access, encryption and retention [WA-T]. Lead: `securityClassification`
  in the field file.
- **External Ids** on every object that is loaded or integrated, so repeated runs update and
  do not duplicate [DG-INT].
- **Formula fields**: 3,900 characters including spaces and comments (**verify**). A formula
  can show a value from a field its reader cannot see, because access is checked on the
  formula field itself [ADV `formula-fields-bypass-fls`]: classify it like its inputs.
- **Encryption changes behaviour.** Shield Platform Encryption needs a licence. A field
  encrypted with its default scheme cannot be filtered, sorted or aggregated; the
  deterministic scheme allows exact match. Test formulas, reports and queries that touch the
  field first, and encrypt by classification, not "every field" [WA-T] [P-DATA].
- **Names and addresses**: permissive validation, addresses stored as entered, tests with
  single names, accents and non-Latin scripts [WA-F].
- **History**: standard field history keeps 18 months in the UI; Setup Audit Trail keeps 180
  days, so export it [WA-T].
- **Deleting a field** renames it with `_del` and keeps it for 15 days, where it still counts
  against the object's field limit [ADV `deleted-field-rename`]. A delete is a stop-and-ask
  change: find what references the field first, old flow versions included.

Check: `[check: doc.description-missing, doc.help-text-missing]`; by hand, `required` is
true only with a recorded reason.

## 4. Picklists

- A restricted picklist rejects values outside its set; an unrestricted one accepts any text
  from the API, which no report can then group [ADV `restricted-picklist-rejects-new-values`].
  Restrict new picklists unless free text is intended. By hand: `restricted` in `valueSet`
  (lead).
- A restricted picklist also rejects a value that is merely inactive, even on a record that
  already holds it [ADV `restricted-picklist-rejects-inactive`].
- A value must be available on the record type as well, or integration writes fail; and a
  picklist default is applied on API inserts too (both field-reported).
- A global value set is always restricted, and promoting a picklist to one cannot be undone
  (**verify**): stop and ask.

Check: before a load or an integration go-live, read the active values and the
`restrictedPicklist` flag from describe, and the values enabled on the record type the
writer uses.

## 5. Validation rules

Rules on the platform run for every way in: page, API, loads and integrations [P-AUTO].

1. Write the message for the user: what is wrong and what to do
   (`documentation-and-naming.md`, section 4).
2. Before you add or tighten a rule, run its condition as a read-only query against existing
   records and count who would fail. Explain every unexpected hit or miss (field-reported: a
   rule built from two traced examples missed about a hundred real cases and wrongly caught
   three).
3. Protect legacy data: scope a stricter rule to records created after a cut-off date so old
   records stay editable (field-reported). Before you write a filter or a rule on a field,
   profile its real values by frequency and count the blanks (field-reported: a data-quality
   report missed the org's most common placeholder value, and a filter read a field that was
   empty in real data).
4. Check what else writes the object: integrations, packages and tests fail on a new rule.
   In an NPSP org a rule can make package batches fail silently (skill `salesforce-npsp`).
5. If a load or integration must skip the rule, bypass through a custom permission:
   `NOT($Permission.Bypass_Name)` in the condition, granted by permission set (**verify**).
   Never name a profile or a user.
6. Deploy a new rule active to the sandbox and inactive to production. Activation is its own
   approved step, with the runbook written first (field-reported).

Check: `[check: rule.no-error-message, rule.hard-coded-id, rule.profile-or-user-literal,
doc.description-missing]`. After deploy, save one record
that must fail and one that must pass, as the intended user.

## 6. Duplicate rules

- Read what is active before a load or a new integration:
  `SELECT DeveloperName, IsActive, SobjectType FROM DuplicateRule WHERE IsActive = true`.
  Standard Account, Contact and Lead rules are active on a new org
  [ADV `duplicate-rules-block-the-save`].
- A blocking rule stops the save after before-save flows and triggers have run [ADV].
- An alert-type rule blocked creates by a non-admin integration user (field-reported). Test
  the exact client and user before go-live.
- Limits per object: 5 active duplicate rules, 3 matching rules per duplicate rule
  (**verify**).

Activating a blocking rule or changing a matching rule is a stop-and-ask change.

## 7. Volume and skew

- Keep any one parent under about 10,000 children and any one owner under about 10,000
  records: beyond that, queries slow down and saves hit record locks [WA-RC] [F-SHARE]. An
  integration user that owns everything is the usual cause.
- Leads to measure it:
  `SELECT OwnerId, COUNT(Id) FROM Opportunity GROUP BY OwnerId HAVING COUNT(Id) > 10000` and
  `SELECT AccountId, COUNT(Id) FROM Contact GROUP BY AccountId HAVING COUNT(Id) > 10000`.
- A filter is selective only on an indexed field: Id, Name, owner, created and modified
  dates, lookups, and fields marked External Id or Unique (**verify**). Reports and list
  views on large objects need the same care.

## 8. Irreversible or far-reaching: stop and ask

Enabling Person Accounts (skill `salesforce-nonprofit-cloud`; check with
`SELECT IsPersonAccount FROM Account LIMIT 1`, which compiles only when they are on);
converting a lookup to master-detail or back; changing a field's type; deleting a field;
promoting a picklist to a global value set; a new required field or rule on an integrated
object; encrypting a field used in filters, formulas or matching; a new object that overlaps
a standard or package object [WA-T].

## 9. Leave a dictionary

Every object and field you add appears in the data dictionary with its description and
classification (`salesforce-delivery/references/handover-and-support.md`). Draw a schema
diagram only for the scope that changed, with a title and a stated purpose [D-ERD].

## Sources

- [SK-OBJ] https://github.com/forcedotcom/sf-skills/blob/main/skills/platform-custom-object-generate/SKILL.md
- [TH-DM] https://trailhead.salesforce.com/content/learn/modules/data_modeling/objects_intro
- [DG-INT] https://architect.salesforce.com/docs/architect/decision-guides/guide/data-integration
- [WA-T] https://architect.salesforce.com/docs/architect/well-architected/guide/trust.html
- [WA-F] https://architect.salesforce.com/docs/architect/well-architected/guide/fairness.html
- [WA-RC] https://architect.salesforce.com/docs/architect/well-architected/guide/resource-and-cost-optimization.html
- [P-AUTO] https://architect.salesforce.com/docs/architect/well-architected/guide/operational-excellence-automation-patterns
- [P-DATA] https://architect.salesforce.com/docs/architect/well-architected/guide/trust-data-protection-patterns
- [F-SHARE] https://architect.salesforce.com/docs/architect/fundamentals/guide/platform-sharing-architecture
- [D-ERD] https://architect.salesforce.com/docs/architect/reference-diagrams/guide/data-model-notation
- [ADV] Torque advisory catalogue (`torque advisory notes --command TEXT`); entry ids inline.
  `layout-required-is-not-api-required`, `deleted-field-rename`,
  `restricted-picklist-rejects-new-values` and `duplicate-rules-block-the-save` are marked
  verified live there; the others practitioner.
- **verify** items: formula limits (https://help.salesforce.com/s/articleView?id=sf.formula_field_limits.htm&type=5),
  picklists (https://help.salesforce.com/s/articleView?id=platform.picklist_limitations.htm&type=5),
  duplicate rules (https://help.salesforce.com/s/articleView?id=sales.duplicate_rules_overview.htm&type=5),
  custom permission bypass (https://trailhead.salesforce.com/content/learn/modules/validation-rules/bypass-your-validation-rules),
  indexes (https://developer.salesforce.com/docs/platform/salesforce-large-data-volumes-bp/guide/ldv-deployments-infrastructure-salesforce-query-optimizer.html).
- field-reported: lessons from real client work with Torque and its predecessor (one
  consultant, about a dozen clients, mostly sandboxes): working notes, an after-action review
  of 166 rework and defect episodes, and a classification of 273 failed Salesforce CLI calls.
  Not re-tested for this release.
