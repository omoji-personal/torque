# Integration and connectors

Use this before you build on a connector, call another system, accept data from one, or test
anything that can reach outside the org. State described: Winter '27 (API 68.0), checked
2026-11-14. A tag such as `[check: secret.looks-like-secret]` names the `torque check
metadata` rule that tests the statement. **verify** = not confirmed in a
fetched primary source: verify in the org or the current documentation. field-reported =
seen in client work, usually one org.

## 1. Connector first, and read before you design

1. Find what already connects the two systems: a vendor connector, an installed package, the
   client's middleware or ETL tool. Use it before you build ("Avoid unnecessary data
   replication"; put complex cross-system orchestration in middleware) [DG-INT].
2. Before you settle any automation that depends on a connector, read three things and say
   what each did not confirm (field-reported):
   - the vendor's documentation;
   - the connector's own mapping and settings in the org;
   - the existing data it has already written.
   One question (does the connector overwrite the stage of an existing record) was answered
   from the connector's logic and the data, without a live transaction.
3. Before you recommend or design around a third-party tool, confirm on the vendor's pages
   that day: where it processes data, whether its provider permits the integration, and
   which plan has the API the design needs (field-reported: one tool rendered documents in
   the vendor's cloud and failed an "inside Salesforce" requirement).
4. An installed package is third-party code with the permissions you give it: question any
   that wants Modify All Data or View All Data [WA-T]. A beta package version cannot go to
   production: check `SubscriberPackageVersion.IsBeta` on `InstalledSubscriberPackage`
   through the Tooling API (field-reported).

Check: the design note has a before-and-after table from the same scenarios on the old and
the new behaviour.

## 2. Pick the pattern

Source: [F-INT].

| Direction | Timing | Pattern | Best tools |
| --- | --- | --- | --- |
| Salesforce calls out, needs the answer | Synchronous | Request and reply | External Services; Apex callout with a named credential |
| Salesforce tells another system | Asynchronous | Fire and forget | Platform event published by a flow, Pub/Sub API, Change Data Capture |
| Another system calls Salesforce | Either | Remote call-in | REST, Composite, Bulk API 2.0 above about 2,000 records |
| Either, in bulk | Asynchronous | Batch data synchronization | ETL or middleware over Bulk API 2.0 |
| Show outside data without copying it | Synchronous | Data virtualization | Salesforce Connect external objects |

Avoid for new work: Outbound Messaging; Data Loader, the import wizard or CLI data commands
as the base of an integration; PushTopic and generic streaming; Salesforce to Salesforce
[DG-INT] [DG-EDA].

## 3. Credentials and identity

- Every callout authenticates through a named credential with an external credential whose
  principals are granted by permission set [P-IAM]. "Never embed credentials in application
  code or configuration files" [WA-T]: not in Apex, flows, custom settings, custom metadata
  or version control.
- One app registration and one integration user per integration, on least privilege
  (`access-and-sharing.md`, section 6). New connected apps cannot be created since
  Spring '26; integrations register as external client apps [WA-T].
- Server to server uses the JWT bearer or client credentials flow, never the
  username-password flow, which retires on 2027-02-20 with the user-agent flows [REL-W27].
- Never put a token in a callout address. A failed anonymous Apex callout echoed the whole
  endpoint, token included, into the session (field-reported). Send it in a header or the
  body, and confirm the host is allowed first. By hand: search Apex and scripts for `token=`
  or `access_token=` inside an endpoint string. `[check: secret.looks-like-secret]` covers
  custom metadata, settings and credential files only.
- A callout to a new host needs a remote site setting or a named credential, and an external
  credential needs principal access for the user who runs it (field-reported: an
  "Unauthorized endpoint" error and three "not accessible" results).
- Before you print custom metadata, custom settings or sample request bodies retrieved from
  an org, reduce them to key names and hosts: masking by field name alone leaked a
  credential fragment from a "sample request" field (field-reported).

The three files that deploy together [MD-TYPES]: an `*.externalCredential-meta.xml` with
`authenticationProtocol` and `externalCredentialParameters` (principals are parameters of
type `NamedPrincipal` or `PerUserPrincipal`); a `*.namedCredential-meta.xml` of
`namedCredentialType` `SecuredEndpoint` whose `namedCredentialParameters` give the `Url`
and the `externalCredential`, with `allowMergeFieldsInHeader` and `allowMergeFieldsInBody`
false unless a header or body needs them; and a permission set with
`externalCredentialPrincipalAccesses` (`externalCredentialPrincipal`, `enabled`). Secret
values are entered in Setup, not in the files. A `Legacy` named credential with a
`username` and `password` is not for new work. To learn the exact shape, set one up in a
sandbox and retrieve it.

Check: `namedCredentials/` and `externalCredentials/` hold the endpoint and the
authentication; a search of the change finds no `Authorization` literal or secret;
`[check: secret.looks-like-secret]`.

## 4. Writes that can be repeated

- Write with upsert on an external Id, so a retry or a replay updates and does not duplicate
  [F-INT]. Give every integrated object an external Id field.
- Carry a unique message Id and reject one already processed. Replayed events keep their
  replay Id, so the consumer must de-duplicate [F-INT].
- "Never issue an unchecked retry on a non-idempotent write" [F-AGINT].
- A save with unchanged values is still a write: it fires automation and restamps who last
  modified the record [ADV `no-op-update-is-not-free`]. Send only records that changed.

## 5. Errors, timeouts and limits

- In request and reply the caller owns retries. Retry timeouts, 5xx and 429 responses with
  growing waits and a ceiling; do not retry other 4xx responses [F-INT] [WA-R].
- A composite call returns HTTP 200 even when a part failed: read the body [F-INT]. A bulk
  job can finish as complete with thousands of failed rows: read the failed-record results
  for the job id and compare counts before and after [ADV `bulk-partial-failure`].
- Set an explicit timeout on every callout: a few seconds where a user waits [WA-R].
- A callout cannot follow uncommitted database changes in the same transaction, and a
  callout from a trigger must go through asynchronous work [DG-INT].
- Bulk API 2.0 runs batches in parallel: sort child rows by parent to avoid lock errors
  [F-INT]. A lock error is usually about the parent, not the row [ADV `unable-to-lock-row`].
- Watch API use against the org's daily allocation, with warnings at 70% and 85% [P-STOR]:
  `sf org list limits --target-org ALIAS` [PLUGIN-LIMITS].
- Platform events: publish after commit unless the event must survive a rollback;
  subscribers must cope with a repeat delivery; do not chain your own flows with events
  [DG-EDA].

## 6. Data coming in

field-reported, one forms integration: date and time values arrived as UTC epoch numbers and
lost the time, multi-select values arrived comma-separated, and yes or no matching ignored
case. Land the raw text, convert it in a flow, and reconcile mapping sheets by source field,
not by column name. Restricted picklists reject values outside their set
(`data-model.md`, section 4).

## 7. Sandboxes and what test data can trigger

"Sandboxes never call production endpoints" is the rule [P-DEVOPS]; do not assume it holds.

Before you create or update test records, above all after a sandbox refresh:

1. List everything that fires on create and update for the object, Process Builder and
   package automation included (`salesforce-flow/references/build-checklist.md`, step 1).
2. Read how each integration picks its endpoint. Field-reported: integrations chose
   endpoints by org name and fell back to production settings when the sandbox was not
   listed, after the first report had said nothing could send.
3. Set email Deliverability to system email only, and switch text-message gateways off.
4. Give test contacts undeliverable addresses (a reserved domain such as `example.invalid`)
   and leave marketing and subscription fields unticked.
5. Find who a connector alerts on failure: each failed connector step emailed the forms
   account owner, and a client admin received up to 28 alerts (field-reported).
6. After the first record, list the day's background jobs and confirm nothing left the org.

Check: the Deliverability setting; `AsyncApexJob` rows created since the test started.

## 8. Tests to run on purpose

Run tests on both sides of the integration [TH-REL]. Field-reported cases that each found a
defect:

| Test | What it caught |
| --- | --- |
| Send the same change twice | Duplicate records |
| Wrong external key, and a blank one | A blank key completed silently |
| A change that originates from the integration's own write | An echo loop |
| The reject path as well as the approve path | Reject failed while approve passed |
| Read the result in the other system, not the sync status | "Synced" shown for edits the other system rejected |
| Read the vendor's own run log after each test | Failed connector steps on 26 of 35 runs that nobody had counted as failures |
| Match an existing person or record, then read its old values | A redesign that would have overwritten details already there |

Record a stand-in trigger (for example queue rows created by hand while webhooks were down)
as synthetic, not as a live test.

## 9. In a client's third-party account

field-reported practice:

- Confirm the site is the client's and whether it is test or production before you type
  anything: a look-alike test site of another organization was signed in to by mistake.
  Compare the address in the connector's settings with the browser tab.
- Inventory who owns the tokens, forms and authorizations first. One production
  authorization can serve every live connector.
- Touch only your own test item. Delete, revoke and reconnect are the consultant's clicks.
- Where there is no test tenant, use read-only checks; each change needs its owner's yes and
  a note of before, after and how to revert.
- In browser automation, find a control by its element, never by coordinates, when a
  destructive control sits beside it.

Stop and ask before: any new flow of personal or regulated data out of the org; creating
credentials or app registrations; widening scopes or an integration user's permissions;
pointing anything at a production endpoint; choosing a custom build over an existing
connector.

## Sources

- [F-INT] https://architect.salesforce.com/docs/architect/fundamentals/guide/integration-patterns
- [MD-TYPES] Salesforce's Metadata API type definitions as shipped with the Salesforce CLI
  (`@salesforce/types` 1.8.0, `ExternalCredential`, `NamedCredential`,
  `PermissionSetExternalCredentialPrincipalAccess`), read 2026-11-14.
- [F-AGINT] https://architect.salesforce.com/docs/architect/fundamentals/guide/agentic-integration-patterns
- [DG-INT] https://architect.salesforce.com/docs/architect/decision-guides/guide/data-integration
- [DG-EDA] https://architect.salesforce.com/docs/architect/decision-guides/guide/event-driven
- [WA-T] https://architect.salesforce.com/docs/architect/well-architected/guide/trust.html
- [WA-R] https://architect.salesforce.com/docs/architect/well-architected/guide/reliability.html
- [P-IAM] https://architect.salesforce.com/docs/architect/well-architected/guide/trust-identity-access-patterns
- [P-STOR] https://architect.salesforce.com/docs/architect/well-architected/guide/resource-and-cost-optimization-storage-api-patterns
- [P-DEVOPS] https://architect.salesforce.com/docs/architect/well-architected/guide/operational-excellence-devops-patterns
- [REL-W27] https://www.salesforce.com/blog/winter-27-release-architect-highlights/
- [TH-REL] https://trailhead.salesforce.com/content/learn/modules/advanced-salesforce-release-readiness-strategies/test-new-features-before-a-release
- [PLUGIN-LIMITS] https://raw.githubusercontent.com/salesforcecli/plugin-limits/main/README.md
- [ADV] Torque advisory catalogue (`torque advisory notes --command TEXT`); entry ids inline.
- field-reported: lessons from real client work with Torque and its predecessor (one
  consultant, about a dozen clients, mostly sandboxes): working notes, an after-action review
  of 166 rework and defect episodes, and a classification of 273 failed Salesforce CLI calls.
  Not re-tested for this release.
