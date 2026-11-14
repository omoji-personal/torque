# Pages, layouts, list views, reports and dashboards

Use this when a change must be seen or used by people: a field on a page, a new page, a list
view, a report or a dashboard. State described: Winter '27 (API 68.0), checked 2026-11-14.
`[check]` = testable in the source files. **verify** = not confirmed in a fetched primary
source: verify in the org or the current documentation. field-reported = seen in client
work, usually one org. "House standard" = this playbook's convention where no Salesforce
rule was found. Where this file gives no element name, retrieve an existing component of the
same kind and read its file: do not write the shape from memory.

## 1. Find the page the users actually see

A field is not delivered until it is on the page its users open.

1. Ask which app, record type and profile the users work in.
2. Find the Lightning record page assigned for that combination: App Builder > the page >
   Activation shows org default, app default, and assignments by app, record type and
   profile (**verify**). A page that is deployed but not activated is not shown (**verify**).
3. Open the page's file and see how it renders fields:
   - field sections placed on the page itself (Dynamic Forms; lead: `force:recordFieldSection`
     and field items in the flexipage file), or
   - the Record Detail component (lead: `force:detailPanel`), which shows the page layout
     assigned to the user's profile and record type.
4. Put the field where that page reads it. Adding it to a page layout does nothing on a page
   that uses Dynamic Forms, and the reverse.

Check: signed in as an intended user, on a record of the right record type, the field shows
and can be edited or not as designed.

## 2. Lightning record pages

- **Form choice** [DG-FORMS]: "If you can get all of your user inputs from a single-screen
  form, start with Dynamic Forms"; a screen flow for several screens; a Lightning web
  component for custom events, form-specific validation or fine styling. Dynamic Forms show
  and hide fields by condition but cannot make a field required or read-only by condition,
  cover one object, and are not available on Experience Cloud pages.
- **Few variants.** Prefer a visibility rule on a component to another copy of the page
  (house standard). Changing page assignments or default apps for all users is a
  stop-and-ask change.
- **Phone-only content** needs only a visibility rule on `$Client.formFactor`; it is
  additive and leaves the desktop page unchanged (field-reported). A phone rule cannot be
  proven in a desktop browser or the App Builder preview: record the gap, and verify the
  field's access and the page's phone assignment (field-reported).
- **Edit the file surgically** (field-reported: a broad clean-up of `label` properties,
  meant to remove one tab label that blocked deploys, wiped every field-section header on a
  live page during user testing). Start from a fresh retrieve, change only the exact property
  and value, and after deploying retrieve again. `[check]` the count of section labels equals
  the baseline.
- **Assignment in metadata**: app-level assignments are `profileActionOverrides` in the app
  file; they are not removed by a destructive manifest and must be added to change sets by
  hand (**verify**). To learn the shape for this org, activate in a sandbox and retrieve the
  app and the object.
- **Performance**: use Analyze in Lightning App Builder; keep the first view light and move
  secondary components to tabs that are not the default (**verify**).
- **Rich text** entered on a form: sanitise it on the server for every source, not only in
  the form [DG-FORMS].
- **Inclusive forms**: visible labels (placeholder text alone fails), keyboard-only
  completion, base components first [WA-F].

## 3. Page layouts and actions

- Profiles still carry page layout assignment [WA-T]. A layout change reaches users by
  profile and record type: confirm which layout the intended users get.
- Required on the layout is not required to the API [ADV `layout-required-is-not-api-required`].
- A quick action stays on a layout after its removal unless an empty `platformActionList` is
  deployed first (field-reported).
- A managed package's layouts retrieve only when the namespace is inside the layout name
  (field-reported). Do not edit a package's layout; assign your own.
- A screen flow placed on a page is hidden from users who lack Run Flows
  (`salesforce-flow/references/gotchas.md`, item 9).

## 4. List views

House standard, with one sourced rule: a list view on a large object needs a selective,
indexed filter, like any query [DEV-LDV] (**verify**).

| Convention | Check |
| --- | --- |
| Reuse or adjust an existing view before adding one | List the object's views first: `sf org list metadata --metadata-type ListView --target-org ALIAS` (confirm the type name with `sf org list metadata-types`) |
| Name it for the question it answers ("My open cases due this week"), never "Copy of" or a person's name | Read the label |
| Share it with the group that needs it, not with everyone by default, and never leave a team view private to its builder | Open the view's sharing as a member of that group |
| Few columns, in the order people scan; filter on indexed fields | Count columns; read the filter |
| Show no field the audience should not see | Compare the columns with field access for the audience |
| Do not change or delete a shared view other people use without asking | Ask its owner or the admin |

When two users see different rows or columns in the same view or related list, compare their
field read access on every filter and sort field before anything else (field-reported).

## 5. Reports

House standard except where a source is given.

| Convention | Check |
| --- | --- |
| Search for an existing report that answers the question before you build one | Describe `Report` (`sf sobject describe --sobject Report --target-org ALIAS`), then query by name and folder with the fields it lists |
| Name states the question; description states the audience, the decision it supports and the story or ticket | Read both; a description that repeats the name fails |
| Lives in a shared folder named for its audience or function, not in a private or unfiled one | Find its folder; open the folder as a member of the audience |
| Folders are shared with groups or roles, not with named individuals | Read the folder's sharing |
| Uses the object and the date that match the question | Compare one total with a count query on the same filter. In nonprofit orgs see the reporting notes in skills `salesforce-npsp` and `salesforce-nonprofit-cloud` (cash and commitments are different numbers) |
| Filters are selective on large objects | Run it at production volume, not only in an empty sandbox |
| Sensitive fields appear only for audiences that may see them; exports of sensitive objects are watched | Compare columns with field classification [P-DATA] |
| Do not edit or move a report others use; save a new one or ask | Ask its owner |

- Check the number where the business reads it: the exported report, the dashboard tile or
  the page, not only the field or the job status (field-reported: an integration showed
  "Synced" for edits the other system had rejected).
- Filter values in report metadata can hold people's names: do not print report filters into
  notes or a session that must stay free of personal data (field-reported).
- Reports and dashboards are edited by users in production. Retrieve before you change one,
  and do not overwrite from an older copy. A community pipeline deploys them only when they
  are missing for this reason [HARDIS].

## 6. Dashboards

| Convention | Check |
| --- | --- |
| Every measure the client steers by is on a dashboard with a named owner [P-AUTO] | The owner is named in the dashboard's description or the hand-over note |
| The running user is chosen on purpose and recorded | Retrieve the dashboard and read who it runs as; view it as a member of the audience and confirm they should see those numbers |
| A dashboard that runs as the viewer is used where people must see only their own records | Lead: `SELECT COUNT() FROM Dashboard WHERE Type = 'LoggedInUser'` counts them |
| Each component's source report follows section 5 | Open the source reports |
| Folder and sharing as for reports | Open it as a member of the audience |

Changing a dashboard's running user changes what every viewer sees: stop and ask.

## 7. Prove it as the user

- Sign in as an intended user and open the page, view, report or dashboard. Use
  `torque browser login` to sign the browser in (see `torque browser login --help`), then
  "Log in as" or a mirrored test user (`access-and-sharing.md`, section 7).
- Lightning caches page renders: reload fully, or start a new session after a deploy, before
  you decide a page change failed (field-reported).
- In browser automation, field labels on a record page sit inside shadow roots: matching
  finds them but page text does not include them [ADV
  `lightning-field-labels-live-in-shadow-dom`].
- Say which user, which app and record type, which device, and what you could not test.

## Sources

- [DG-FORMS] https://architect.salesforce.com/docs/architect/decision-guides/guide/build-forms
- [WA-T] https://architect.salesforce.com/docs/architect/well-architected/guide/trust.html
- [WA-F] https://architect.salesforce.com/docs/architect/well-architected/guide/fairness.html
- [P-AUTO] https://architect.salesforce.com/docs/architect/well-architected/guide/operational-excellence-automation-patterns
- [P-DATA] https://architect.salesforce.com/docs/architect/well-architected/guide/trust-data-protection-patterns
- [DEV-LDV] Query selectivity (read as a search extract): https://developer.salesforce.com/docs/platform/salesforce-large-data-volumes-bp/guide/ldv-deployments-infrastructure-salesforce-query-optimizer.html
- [HARDIS] sfdx-hardis, metadata not overwritten by a pipeline (community): https://sfdx-hardis.cloudity.com/salesforce-devops-config-overwrite/
- [ADV] Torque advisory catalogue (`torque advisory notes --command TEXT`); entry ids inline.
- **verify** items: page activation (https://trailhead.salesforce.com/content/learn/modules/lightning_app_builder/lightning_app_builder_recordpage),
  assignment metadata (https://developer.salesforce.com/docs/atlas.en-us.api_meta.meta/api_meta/meta_profileactionoverride.htm),
  page performance (https://help.salesforce.com/s/articleView?id=lightning_page_performance.htm&type=5).
- field-reported: lessons from real client work with Torque, not re-tested for this release.
