# Access and sharing

Use this whenever a change adds a field or component, grants or reduces access, touches
sharing, or involves an integration user. State described: Winter '27 (API 68.0), checked
2026-11-14. A tag such as `[check: access.field-without-grant]` names the `torque check
metadata` rule that tests the statement. **verify** = not confirmed in a
fetched primary source: verify in the org or the current documentation. field-reported =
seen in client work, usually one org. Queries marked "lead" were not run for this release.

## 1. The rule that is broken most: a new field ships with its access

A field deployed through the Metadata API or a change set gets no field-level security
unless the same deployment grants it [ADV `fls-not-automatic`]. The deploy is green and the
field is invisible in pages, list views, reports and queries. In the field record this was
the most frequent build miss, with hours lost to wrong theories (field-reported).

Which fields: a new custom field that takes field-level security. Not required fields and
not master-detail fields: they carry no field permissions, and an entry fails the deploy
[SK-PS] [ADV]. `torque check metadata` also skips fields on custom metadata types,
platform events and custom settings, and fields of a managed package (rule
`access.field-without-grant`); give those types the access their own model uses.

Do this for every such field:

1. Add a `fieldPermissions` entry (`field` as `Object__c.Field__c`, `readable`, `editable`)
   to a permission set in the same deployment. Create the permission set if none fits.
2. `editable` true only for fields users enter, and then `readable` true as well
   (**verify**: edit without read is refused); a formula or roll-up summary field's entry
   has `editable` false, or the whole deploy fails [SK-PS] [ADV].
3. Never invent a field name by pattern: grant only fields that exist in the change or in
   the org [SK-PS].
4. Assign the permission set to the people who need the field, or say who must.
5. After deploy, query, do not look: your own view is not evidence either way.

```sh
sf data query --target-org ALIAS --query "SELECT Field, Parent.Name FROM FieldPermissions WHERE Field = 'Object__c.Field__c'"
torque advisory evidence --target-org ALIAS --field Object__c.Field__c --permset Permission_Set_Name
```

`FieldPermissions` and `PermissionSetAssignment` are standard-API objects: with the Tooling
flag the query fails [ADV `tooling-api-refuses-permission-objects`]. To filter permission
rows by field, use `=` or `IN` with the full `Object.Field` name: `LIKE` on `Field` is
refused (field-reported). A required field has no rows and needs none.

`[check: access.field-without-grant, access.formula-field-editable]`.

In Setup, the same habit: enable "Field-Level Security for Permission Sets during Field
Creation" in User Management Settings so the new-field wizard grants through permission
sets (**verify**). View All Fields on an object exists (**verify**): do not use it to skip
this rule unless the client asked for that breadth.

## 2. Permission sets, not profiles

- "Grant functional access through permission sets and permission set groups rather than
  profiles." Profiles remain for page layout assignment, record type defaults, login hours,
  IP ranges and app visibility [WA-T]. The planned retirement of permissions on profiles was
  cancelled; Salesforce still recommends the permission-set-led model (**verify**).
- `[check: access.profile-in-change]`. A profile belongs in a change only for what still
  lives there (the list above), never for object, field or system permissions. Deploy the
  narrowest file: a profile deploy applies what the file says, explicit `false` values
  included, and leaves the rest alone [ADV `full-profile-deploy`]. Check: `git diff` of the
  profile file shows only the layout, record type or app entries you meant.
- Name a permission set for the task, bundle sets into a group per persona, and use a muting
  permission set for exceptions [TH-PSG] [P-IAM].
- A new component needs its own grant too: object permissions, tab, Apex class access,
  custom permission, flow access, record type. field-reported: testing as staff found a
  missing Apex class grant for a page controller that every admin test had passed.
- "Grant the minimum level of access that's necessary for each user, integration, and
  automated process" [WA-T]. View All Data, Modify All Data and Manage Users "require
  security review", and "Do not add unrequested user permissions to work around a dependency
  error" [SK-PS].
- View All Data opens records, not fields [ADV `view-all-data-vs-fls`].
- Take user permission names for a permission set file from a describe of `PermissionSet`
  in the target org (its `Permissions...` fields), not from memory: a deploy failed on
  "Unknown user permission" (field-reported). Some exist only where a feature is enabled.

Check, lead: `SELECT COUNT() FROM PermissionSetAssignment WHERE
PermissionSet.PermissionsModifyAllData = true AND Assignee.IsActive = true`, then account for
each holder.

## 3. Assignment is a separate fact

- A permission set held through a group has no assignment row of its own: a count filtered
  on the member set's name returns 0 while users hold it. Query `PermissionSetGroupComponent`
  as well [ADV `psg-assignment-names-the-group`].
- Assignments are data in the org, not part of the deploy. Sandbox-only assignments are lost
  on a refresh (field-reported).

## 4. Record access

Access adds up across four layers (organization, object, field, record). Nothing denies in
general; only restriction rules and muting inside a group take away [WA-T] [P-IAM].

- Org-wide defaults: Private for sensitive objects, Public Read Only for reference data,
  never Public Read/Write for convenience. Tightening later forces a sharing recalculation
  [WA-T] [P-IAM]. Lead: `SELECT QualifiedApiName, InternalSharingModel, ExternalSharingModel
  FROM EntityDefinition WHERE IsCustomizable = true`.
- Use the narrowest mechanism that meets the need [F-SHARE]:

| Requirement | Mechanism |
| --- | --- |
| Baseline for an object | Org-wide default, the most restrictive that works |
| Managers see their reports' records | Role hierarchy (it models data access, not the org chart) |
| One group needs another group's records | Owner-based sharing rule (300 per object) |
| Access depends on a field value | Criteria-based sharing rule (50 per object, shared with guest rules) |
| A few named people on one record | Account, opportunity or case team |
| One-off share by the owner | Manual share (lost on owner change unless the Winter '27 setting keeps it [REL-W27]) |
| Hide some records a user could otherwise see | Restriction rule (few per object, **verify** the number) |
| Narrow the default view without removing access | Scoping rule: not a security control |
| Signed-in site users at volume | Sharing set or share group |
| Site visitors who are not signed in | Guest user sharing rule: read only, as narrow as possible |
| Logic no declarative tool expresses | Apex managed sharing, last |

- Guest users: guests cannot own records, their org-wide defaults stay Private, and a guest
  sharing rule opens every matching record at once. Give guests Read and Create at most,
  never View All or Modify All [F-SHARE] (**verify** details in Help).

Check which rules exist in `sharingRules/` metadata and Setup > Sharing Settings. Any change
to an org-wide default, the role hierarchy or a sharing rule is a stop-and-ask change.

## 5. Reducing access safely

Field-reported: a first pass that compared only object and system permissions missed a tool
that would have broken.

1. Compute effective access for the people affected: profile plus permission sets, across
   system, object, field, tab, app, Apex and Visualforce access, and folders.
2. Find what they actually do: records they own or edit, reports they run, login history.
3. Ship a permission set for what they would lose and still need, first.
4. Test their real journeys in the sandbox as them, then make the cut.

Check: the journeys pass as that user in the sandbox after the change.

## 6. Integration and API-only users

- One dedicated user per integration, on least privilege, never a shared "API user" with
  Modify All Data and never a person's account [WA-T] [P-IAM].
- Salesforce's pattern: the Salesforce Integration user licence with the "Minimum Access -
  API Only Integrations" profile, plus a permission set holding only what that integration
  needs (**verify** licence names and counts).
- field-reported, one forms connector: the standard API-only profile failed with "record
  type missing". It worked with a custom profile on that licence with a default record type,
  a permission set on the integration licence, a duplicate-rule filter that excluded the
  profile, and the app pre-approved for the user.
- An API-only user cannot be opened in a browser (**verify**). Test page behaviour with a
  separate test user.
- Site guest users needed Read on User External Credentials before a public callout through
  an external credential worked (field-reported).
- When work on a production org ends: `sf org logout` only deletes the local authorization.
  It does not revoke the token. Revoke the session in the org first if access must end
  (field-reported).

## 7. Verify effective access

Configuration, assignment, effective access, record access and the rendered page are five
separate checks. A query as an administrator proves none of the last three.

| Check | How |
| --- | --- |
| The permission set contains the grant | The file, or `FieldPermissions` and `ObjectPermissions` |
| The user holds it | `PermissionSetAssignment`, directly and through groups |
| The user can open this record | `SELECT RecordId, HasReadAccess, HasEditAccess, MaxAccessLevel FROM UserRecordAccess WHERE UserId = 'USER_ID' AND RecordId = 'RECORD_ID'` (**verify**; it ignores restriction rules) |
| Logic and access together | Apex with `WITH USER_MODE` inside `System.runAs` as a minimum-access user for the negative case (outside it, user mode checks the user running the test); `System.runAs` needs an active user (field-reported) |
| The page shows it | In a browser as that user, one person per user group, on a record someone else owns (field-reported: a user-mode code check "proved" a list that one profile could not see) |

To act as the user in a browser: `torque browser login --as-alias ALIAS-OF-USER` for a test
user with its own CLI alias (no banner: confirm the user in the profile menu), or
`--as-user` and Setup "Log in as" (needs Modify All Data and the org's login-access policy,
**verify**; wait for the "Logged in as" banner). Record which user and which method. "Log in
as" skips login flows, so it is not the user's real sign-in (**verify**). The steps and the
persona checklist: `salesforce-delivery/references/testing-and-uat.md`, section 3.

field-reported findings that only a test as real staff exposed: one field's access, a tester
who lacked the feature, users outside the test group's access tier, and a managed action
that showed "No actions available". And when two users on one profile see different list
results, compare field read access on every filter and sort field of the list, then
permission sets, groups and View All Data, in each org separately.

## 8. Posture checks, read-only

- Health Check: read the score, change nothing during an assessment. "Fix Risks" can break
  integrations: change one setting at a time after a sandbox test [TH-HC]. Lead:
  `SELECT Score FROM SecurityHealthCheck` through the Tooling API.
- Sessions: multi-factor sign-in for production, no shared accounts, IP ranges for admins and
  integration users [P-IAM].

## Sources

- [SK-PS] https://github.com/forcedotcom/sf-skills/blob/main/skills/platform-permission-set-generate/SKILL.md
- [WA-T] https://architect.salesforce.com/docs/architect/well-architected/guide/trust.html
- [P-IAM] https://architect.salesforce.com/docs/architect/well-architected/guide/trust-identity-access-patterns
- [F-SHARE] https://architect.salesforce.com/docs/architect/fundamentals/guide/platform-sharing-architecture
- [TH-PSG] https://trailhead.salesforce.com/content/learn/modules/permission-set-groups/get-started-with-permission-set-groups
- [TH-HC] https://trailhead.salesforce.com/content/learn/modules/security_basics/security_basics_healthcheck
- [REL-W27] https://www.salesforce.com/blog/winter-27-release-architect-highlights/
- [ADV] Torque advisory catalogue (`torque advisory notes --command TEXT`). `fls-not-automatic`
  and `full-profile-deploy` are marked verified live there; the others practitioner.
- **verify** items: field permissions on deploy (https://help.salesforce.com/s/articleView?id=000386377&type=1),
  profile-permission retirement (https://help.salesforce.com/s/articleView?id=003834041&type=1),
  integration user (https://help.salesforce.com/s/articleView?id=platform.integration_user.htm&type=5),
  restriction rules (https://help.salesforce.com/s/articleView?id=platform.security_restriction_rule_considerations.htm&type=5),
  guest users (https://help.salesforce.com/s/articleView?id=sf.networks_secure_guest_user_sharing.htm&type=5),
  record access (https://help.salesforce.com/s/articleView?id=000386023&type=1),
  Log in as (https://help.salesforce.com/s/articleView?id=xcloud.logging_in_as_another_user.htm&type=5).
- field-reported: lessons from real client work with Torque and its predecessor (one
  consultant, about a dozen clients, mostly sandboxes): working notes, an after-action review
  of 166 rework and defect episodes, and a classification of 273 failed Salesforce CLI calls.
  Not re-tested for this release.
