# Flow versions, activation and deploy

Use this before you describe, change, deploy, activate or remove a flow. State described:
Winter '27 (API 68.0), checked 2026-11-14. **verify** = not confirmed in a fetched primary
source: verify in the org or the current documentation. field-reported = seen in client
work, usually one org: run the check.

## 1. Read the org first

A retrieve returns the latest version only, and the latest can be a draft while an older
version runs [ADV `flow-retrieve-latest-only`]. So a retrieved file does not tell you what is
live.

```sh
torque advisory flow --target-org ALIAS --api-name Flow_Api_Name
sf data query --use-tooling-api --target-org ALIAS --query "SELECT DeveloperName, ActiveVersionId, LatestVersionId FROM FlowDefinition WHERE DeveloperName = 'Flow_Api_Name'"
sf data query --target-org ALIAS --query "SELECT ApiName, IsActive FROM FlowDefinitionView WHERE ApiName = 'Flow_Api_Name'"
```

- `FlowDefinition` is a Tooling object. `FlowDefinitionView` is a standard object: with the
  Tooling flag it errors, and some clients show that as zero rows
  [ADV `flowdefinitionview-is-standard-api`].
- `ActiveVersionId` empty: nothing runs. `ActiveVersionId` different from `LatestVersionId`:
  the newest version is not the one that runs.
- To read the content of the active version, open that version in Flow Builder, or list the
  versions through `FlowVersionView` (describe it first for its fields) [ORG-TS].

field-reported: in one org five flows ran an older version than the latest, and a flow marked
"ready for testing" was still a draft. Read the active version before you explain a flow,
test it or hand it over.

## 2. What the status in the file does

| Fact | Evidence |
| --- | --- |
| The `status` element in the file (`Active` or `Draft`) is a request, not a guarantee: whether an `Active` file ends up active depends on org settings, the API version and whether the target already has an active version | [ADV `flow-activation-on-deploy`] |
| Flow files carry no version number in their name; the file's status decides whether the deployed version is activated | **verify** (community source) |
| In production, active flows deploy as inactive unless "Deploy processes and flows as active" is on in Process Automation Settings. With it on, Apex tests must cover a set share of active processes and autolaunched flows; flows with screens are exempt | **verify** |
| A sandbox accepts an active version on deploy | **verify** |
| Deploying a non-active status does not deactivate a flow that is active in the org | **verify** |
| A version that is or was active in the target is not overwritten: the deploy creates a new version | **verify** |
| Deploying while the latest version in the org is a draft overwrites that draft | field-reported |
| A flow deployed as Active landed as Draft where no `FlowDefinition` was deployed with it | field-reported |

So after any flow deploy, "it deployed" tells you nothing about what runs. Read it back.

## 3. Deploy procedure

1. Resolve the target org and whether it is production, a sandbox or something else.
2. Retrieve the flow from the target org now and compare it with the baseline you built
   from. If it changed since you started, stop and reconcile (section 5). Ignore a difference
   that is only element positions (field-reported: retrieves differ in zeroed locations).
3. Check for a draft newer than the active version (`LatestVersionId` against
   `ActiveVersionId`). If it is not yours, ask its author before you deploy: your deploy can
   replace it.
4. Note the version that is active now. It is your way back.
5. Validate with an explicit scope, every time (field-reported: check-only runs caught 28 of
   36 deploy failures, and 7 of 8 failed real deploys had none just before). Sandbox:
   `sf project deploy start --dry-run --metadata Flow:Flow_Api_Name --target-org ALIAS`.
   Production: `sf project deploy validate --metadata Flow:Flow_Api_Name --target-org ALIAS`,
   then `sf project deploy quick --job-id JOB_ID --target-org ALIAS` after the owner's go
   [DR-README]. A validated job id stays usable for 10 days [DR-README]; validate again if
   production changed in between.
6. Deploy the flow together with what it needs (new fields, the permission set). Salesforce's
   own deploy skill orders a split deployment as objects and fields, permission sets, Apex,
   flows as Draft, then activation and verification [SK-DEPLOY].
7. Read back with `torque advisory flow`. If the intended version is not active, activate it
   as its own step (section 4).
8. Run the tests in `build-checklist.md`, step 9, against the active version.
9. Report: flow, org, the version now active, the previous version, the deploy job id, and
   what was tested by whom.

Never pass `--ignore-conflicts`, `--ignore-errors` or `--ignore-warnings` unasked: the first
deploys local files "even if they overwrite changes in the org" [DR-README].

## 4. Activation

| Route | When | Evidence |
| --- | --- | --- |
| Activate in Flow Builder or the flow's version list | Default. Anyone can see and repeat it | Setup |
| Deploy the flow file with its `status` element set to `Active` [SAMPLE-FLOW] | Sandboxes, and production orgs that deploy flows as active | section 2 |
| Deploy a `FlowDefinition` with `activeVersionNumber` | Orgs that deploy flows inactive | field-reported. Salesforce's reference recommends no longer using `FlowDefinition` to activate (**verify**) |
| Update the Tooling `FlowDefinition` record | Scripted activation; version number 0 deactivates | field-reported |

- In production, activation is a change in its own right. Name the flow and version, get the
  go for that step, and say what starts happening when it is on.
- Activating or deactivating automation in production is a stop-and-ask point.
- Turning a flow off: record the version that was active and why, so it can be turned back
  on [WA-RC].
- After activation, read back again and rerun one real path.

Way back: reactivate the version you noted in step 4. That restores the logic. It does not
undo records the new version already changed, email it sent or callouts it made.

## 5. Change an existing flow without disturbing it

1. Start from a retrieve of the target org, never from an old folder or from memory. Name one
   current source folder per build (field-reported: a deploy from an older folder stripped
   the descriptions again).
2. To learn a metadata shape you have not used, retrieve a flow that already uses it. If a
   setting exists only as a checkbox on screen, set it once in a sandbox, retrieve, read the
   attribute, then put it in the file (field-reported).
3. Edit only the elements the change needs. Leave every other element, label and description
   as retrieved. Do not regenerate the flow because writing XML is easy.
4. Diff against the retrieved baseline. The diff shows your change and nothing else.
5. Keep descriptions current in the same edit. Do not add them later in a separate version.
6. Validate, deploy and read back as in section 3.

Two routes produce flow metadata: build in Flow Builder in a sandbox and retrieve, or edit
the retrieved file. Salesforce's own agent skill generates flows only through its metadata
tool and forbids hand-written flow XML [SK-FLOW]; field work with Torque edits retrieved
files. Both start from the org's current definition.

## 6. In an org other people use

- Build from that org's own current retrieve. Re-retrieve each component right before you
  deploy and compare. Deploy only the exact components. Validate first. Afterwards retrieve
  again and confirm the only differences are yours (field-reported, a shared sandbox with
  other teams).
- If the version history shows a colleague editing the same flow, stay read-only until they
  agree.
- The CLI warns about conflicts only on orgs with source tracking (Developer and Developer
  Pro sandboxes, scratch orgs). In Partial Copy and Full sandboxes and in production, a deploy
  overwrites without a warning [DR-README] [PLUGIN-ORG].

Check: the post-deploy retrieve differs from the pre-deploy copy only by your change.

## 7. Removing a flow

Deletion is a separate, approved step with a retrieved baseline first.

- Delete flows by version (`Flow_Api_Name-1`, `Flow_Api_Name-2`, and so on), not by the
  plain name (field-reported).
- Deactivate an active flow before the delete (field-reported: active flows needed a manual
  step first).
- Deploy the removal of Apex classes separately from the flows that call them
  (field-reported: the platform tried the classes first and failed).
- Destructive manifests go on `sf project deploy start` with `--pre-destructive-changes` or
  `--post-destructive-changes` [DR-README]. Leave out `--purge-on-delete` unless asked.

Check: a validate-only run of each removal step passes before the real one.

## 8. Moving off Process Builder and workflow rules

- Build nothing new there. Process Builder reached end of support on 2025-12-31 [WA-RC].
- Migrate, test, activate the flow, then deactivate the original: "Migrate remaining Process
  Builders to Flow; don't just deactivate them" [WA-RC].
- Ask before migrating a rule that has pending time-based actions.

## Sources

- [ADV] Torque advisory catalogue: `torque advisory notes --command "sf project deploy start --metadata Flow:Name --target-org ALIAS"`; entry ids inline.
- [DR-README] Salesforce CLI deploy and retrieve plugin: https://github.com/salesforcecli/plugin-deploy-retrieve/blob/main/README.md
- [PLUGIN-ORG] Salesforce CLI org plugin (source tracking by org type): https://raw.githubusercontent.com/salesforcecli/plugin-org/main/README.md
- [ORG-TS] Salesforce CLI core library, flow version lookup: https://github.com/forcedotcom/sfdx-core/blob/main/src/org/org.ts
- [SK-DEPLOY] Salesforce deploy skill: https://github.com/forcedotcom/sf-skills/blob/main/skills/platform-metadata-deploy/SKILL.md
- [SK-FLOW] Salesforce flow skill: https://github.com/forcedotcom/sf-skills/blob/main/skills/automation-flow-generate/SKILL.md
- [SAMPLE-FLOW] https://github.com/trailheadapps/dreamhouse-lwc/blob/main/force-app/main/default/flows/Create_property.flow-meta.xml
- [WA-RC] https://architect.salesforce.com/docs/architect/well-architected/guide/resource-and-cost-optimization.html
- **verify** items: deploying active flows (https://help.salesforce.com/s/articleView?id=platform.flow_distribute_deploy_active.htm&type=5),
  FlowDefinition (read as an extract of the Japanese page: https://developer.salesforce.com/docs/atlas.ja-jp.api_meta.meta/api_meta/meta_flowdefinition.htm),
  and a community note on flow versions in the Metadata API
  (https://docs.gearset.com/en/articles/2439855-changes-to-flows-in-v44-of-the-metadata-api).
- field-reported: lessons from real client work with Torque and its predecessor, not re-tested for this release.
