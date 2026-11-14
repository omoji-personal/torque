# Checks: before and after a deploy

Three commands, read-only:

| Command | Reads | Calls an org |
|---|---|---|
| `torque check metadata [PATH ...]` | the metadata files about to be deployed | no |
| `torque check result RESULT.json` | a saved `sf project deploy start\|validate\|report --json` result | no |
| `torque check deployed --target-org ALIAS ...` | what a deploy left in the org | yes, read-only, through the Salesforce CLI |

## The route

```sh
sf project deploy start --source-dir force-app --target-org ALIAS --dry-run --json > dry-run.json
torque check result dry-run.json                       # did the validation pass, and why not
torque check metadata force-app --changed-from dry-run.json
# fix, validate again, then deploy and confirm:
sf project deploy start --source-dir force-app --target-org ALIAS --json > deploy.json
torque check deployed --target-org ALIAS --changed-from deploy.json
```

Write the result to a file with `--json`. Do not pipe the deploy through a filter: the pipe's exit code
hides the CLI's (in the field record, 44 percent of failed CLI calls reported exit 0 that way).

## `torque check metadata`

A static check of Salesforce source format (`*.field-meta.xml`, `*.object-meta.xml`, `*.flow-meta.xml`,
`*.validationRule-meta.xml`, `*.permissionset-meta.xml`, `*.permissionsetgroup-meta.xml`,
`*.recordType-meta.xml`, `*.customPermission-meta.xml`, `*.profile-meta.xml`, `*.flexipage-meta.xml`,
`*.layout-meta.xml`, custom metadata records, settings and credential files, `*.cls`, `*.trigger`) and of
Metadata API format where it is cheap (`objects/X.object` with its fields, validation rules and record
types, `flows/X.flow`, `permissionsets/X.permissionset`, ...). Standard library only; no org, no network.
A file that is not well-formed XML is one `file.unreadable` finding, never a crash.

```
torque check metadata [PATH ...] [--changed-from RESULT.json | --baseline DIR | --only Type:Name ...]
                      [--analyzer auto|off|required] [--ignore RULE ...] [--fail-on never|error|warning] [--json]
```

### Scope: say what the change is

A retrieved folder is mostly baseline (in the field record, 2,497 components sat in the deployed folders
against 257 the agent authored), so the check needs to know which components are the change.

| Option | In scope | Notes |
|---|---|---|
| `--changed-from RESULT.json` | components the result reports as Created, Changed or Failed | Recommended. Unchanged and Deleted are counted, not checked. Result entries with no file under PATH are listed. Read with the same tolerant reader as `torque check result`. |
| `--baseline DIR` | new and changed components, by comparison with a copy retrieved before the change | A finding on a changed component says `inherited` (it was already there) or `new in this change`. Flow element descriptions count only elements new in this change. Needed for `page.sections-lost` and for an apiVersion that the change lowers. |
| `--only Type:Name` (repeatable) | the named components | Names not found are listed. |
| a PATH that is a file (with or without folders) | every file and folder given | The project folder around them is read for context (field definitions, grants elsewhere). |
| none | everything under PATH | The first line says so, with the counts and the three options above. |

Field access is looked up in the permission sets and profiles that ship with the change: those in the
result (any state but Deleted), those named with `--only` or given as files, or all of them under PATH for
`--baseline` and for no scope. A grant in a permission set under PATH that is not in the change is named in
the action.

### Output

Text: a first line with the scope and counts; findings grouped by component, most severe first, each with
rule id, file, line, the problem and `Do:` the action; lessons from earlier work when the lessons package
offers them; one line on Code Analyzer; a last line with totals and what was not checked.

`--json` prints the same as one object, schema version 1: [`check-metadata.schema.json`](check-metadata.schema.json).
Every finding has `rule`, `severity` (`error`, `warning`, `note`), `source` (`torque` or `code-analyzer`),
`component` (`type`, `name`, `status`: `new`, `changed`, `failed`, `unknown`), `file` (relative to `base`),
`line`, `message`, `action`, `inherited` (`true`, `false`, or `null` when it cannot be told) and
`confirmed_by` (the analyzer rules that reported the same problem).

Exit codes: 0 the report completed, whatever it found; 1 `--fail-on` was met by a finding that is not
inherited; 2 unusable input (missing path, unreadable result, bad `--only`, unknown rule, bad config);
3 `--analyzer required` and the analyzer did not run.

### Rules

| Rule | Severity | Inspects | Known limits |
|---|---|---|---|
| `file.unreadable` | error | a file that is not well-formed XML, holds a DOCTYPE, or cannot be read | no other rule runs on that file |
| `access.field-without-grant` | error with `--changed-from` or `--baseline`; warning with no scope (the field may not be new) or with `--only` or files; note when a permission set elsewhere in the project grants it and `--only` or the files did not name it | a new custom field that takes field-level security (not required, not master-detail, not on a custom metadata type, platform event or custom setting) with no readable or editable `fieldPermissions` entry in a permission set in the change | grants made in Setup or as data records are invisible; a custom setting is recognised only when its object file is read; with no scope every field counts as possibly new |
| `access.grant-only-in-profile` | warning | the same field granted only by a profile in the change | |
| `access.formula-field-editable` | error | an editable `fieldPermissions` entry for a formula or roll-up summary field | only when the field's definition is in the files |
| `access.profile-in-change` | warning | a profile file in the change | |
| `access.powerful-permission` | warning | Modify All Data, View All Data, Manage Users; View All, Modify All or View All Fields on an object, in a permission set | profiles are not inspected for these |
| `doc.description-missing` | warning | an empty or missing description on a custom field, custom object, flow, validation rule, permission set or group, record type or custom permission | presence only, not whether the text is right; standard and managed components are skipped |
| `doc.description-placeholder` | note | a description that repeats the label or API name, or is TODO, TBD, test, ... | |
| `doc.too-long` | error | field description over 1,000 or help text over 510 characters; permission set or validation rule description over 255 | other types' limits are not checked |
| `doc.help-text-missing` | note | a custom field a user fills in (not formula, roll-up, auto-number, custom metadata, platform event or custom setting) without help text | a field on no page layout is still reported |
| `doc.flow-elements-undescribed` | note | one finding per flow: elements without a description; with a baseline, only elements new in this change | resources (variables, formulas) are not counted; Code Analyzer's MissingDescription covers them |
| `doc.description-names-version` | note | a flow description that names a version number ("v2", "version 3"; not "API version 62.0") | |
| `doc.apex-header-missing` | note | a non-test Apex class or a trigger with no comment above its declaration | method-level ApexDoc is Code Analyzer's (pmd ApexDoc) |
| `flow.no-fault-path` | warning | Create, Update, Delete Records, actions and Apex plug-ins without a fault connector; note for Get Records and Subflow | before-save flows are skipped (they have no fault paths) |
| `flow.work-in-loop` | error | Get, Create, Update, Delete Records, actions or subflows inside a loop body | an element is inside a loop when it can be reached from the loop's next-value connector without passing through the loop, and the loop can be reached again from it (every connector followed, fault connectors included). An element on a path that leaves the loop for good runs at most once and is not reported; Code Analyzer's DbInLoop does report it, so both can appear |
| `flow.no-entry-conditions` | warning | a record-triggered flow with no filters and no filter formula in its Start element | |
| `flow.reruns-on-every-update` | warning | an update-triggered flow with entry conditions and side effects (Create Records, actions, subflows, scheduled paths) that does not require the record to change to meet them, has no Is Changed start filter and no ISCHANGED, PRIORVALUE, ISNEW or `$Record__Prior` in its start filter formula, and has no decision condition that reads `$Record__Prior` (directly or through a formula) | only those elements count: a description, label or unused resource that mentions the prior value does not; a decision that checks the change another way, or one not on the side effect's path, is not told apart |
| `flow.after-save-same-record` | note | an after-save flow whose only data change updates the triggering record | |
| `flow.hard-coded-id` | warning | a 15 or 18 character record Id in a flow string value, formula, filter formula or text template | the Id itself is not printed, only its key prefix |
| `flow.api-version-behind` | note | apiVersion below the project's `sourceApiVersion`; error when the change lowers it below its baseline | another flow being newer is not a reason: a flow's version may be held back on purpose |
| `flow.screen-action-without-trigger` | warning | a screen action no trigger in its screen runs (it deploys and never runs); note for a trigger event name other than `flow__screeninit` or `flow__screenfieldattributechange` | event names other than those two were not observed and may be valid |
| `flow.default-names` | note | default or copied names and labels (Copy_1_of_..., Decision_1, myDecision, New Outcome) | |
| `flow.active-in-source` | note | a flow file asks for Active (a sandbox activates on deploy; production only with "Deploy processes and flows as active"); once per report, on the first such flow | a Draft or Obsolete file gets no note |
| `flow.system-mode-without-sharing` | note | `runInMode` SystemModeWithoutSharing and the description does not mention sharing or system mode | |
| `flow.choice-compared-to-literal` | warning | a formula that compares a choice screen field to a text literal (a formula reads the choice's label, not its value) | decisions are not inspected |
| `flow.lookup-missing-first-record-only` | warning | Get Records that stores output automatically without `getFirstRecordOnly` (the deploy fails) | |
| `rule.hard-coded-id` | warning | a record Id in a validation rule formula | |
| `rule.profile-or-user-literal` | note | a formula comparing `$Profile.Name`, `$User.*` or `$UserRole.*` to a literal | `OwnerId <> $User.Id` is not reported |
| `rule.no-error-message` | warning | a validation rule without an error message | |
| `page.sections-lost` | warning | a Lightning page (labelled field sections and tabs) or layout (labelled sections) with fewer labels than its baseline | needs `--baseline` |
| `name.case-collision` | warning | two components of one type, or two names inside a flow, that differ only by case | |
| `managed.component-in-change` | warning | a component whose own name carries a namespace other than the project's | a field you added to a managed object is not reported |
| `code.no-sharing-declaration` | note | a non-test Apex class without `with`, `without` or `inherited sharing` | |
| `code.test-without-assert` | warning | an Apex test class with test methods and no assertion | assertions in a helper class are not seen; ignore the rule for that class |
| `code.see-all-data` | warning | an Apex test with `SeeAllData=true` | |
| `secret.looks-like-secret` | warning | in custom metadata records, settings, named and external credentials: a value that has a known secret shape, or that sits under a key named like a secret and looks like one | reports the file and key name only, never the value |

Left out, because they cannot be computed honestly from the files: a literal threshold that a decision says
must be configurable (a number in a filter may be right), and acceptance criteria that have no component.
Those belong to review against the change record.

### Code Analyzer

`--analyzer auto` (the default) runs `sf code-analyzer run` through the bundled adapter
(`jsc_loganalyzer.code_analyzer`) on the in-scope flow, Apex and Lightning component files, in the project
folder so a `code-analyzer.yml` there applies, with `--rule-selector Recommended --rule-selector flow`: the
flow engine's MissingFaultHandler, MissingDescription, UnreachableElement and UnusedResource rules are not
tagged Recommended and a default run skips them. `SF_DISABLE_TELEMETRY=true` is set for the run unless
you set it otherwise.

Its findings are merged under their own ids (`code-analyzer:ENGINE:RULE`, severity 1 to 2 error, 3 warning,
4 to 5 note). Where a Torque rule reports the same problem on the same file and element, it is reported once,
as Torque's finding with the analyzer in `confirmed_by`, at the higher of the two severities (`confirmed_by`
then says "severity N, so error"), and `--fail-on` reads that severity:

| Code Analyzer rule | Torque rule |
|---|---|
| flow MissingFaultHandler | `flow.no-fault-path` |
| flow HardcodedId | `flow.hard-coded-id` |
| flow DbInLoop | `flow.work-in-loop` |
| flow TriggerEntryCriteria | `flow.no-entry-conditions` |
| flow DefaultCopy | `flow.default-names` |
| flow SameRecordUpdate | `flow.after-save-same-record` |
| flow MissingDescription | `doc.description-missing`, `doc.flow-elements-undescribed` |
| regex AvoidOldSalesforceApiVersions | `flow.api-version-behind` |
| pmd ApexSharingViolations, ApexUnitTestClassShouldHaveAsserts, ApexUnitTestShouldNotUseSeeAllDataTrue | `code.no-sharing-declaration`, `code.test-without-assert`, `code.see-all-data` |

Ignoring a Torque rule also drops its analyzer twin. When the analyzer is not installed the report says so
in one line, with how to install it (`sf plugins install code-analyzer`; the flow engine needs Python 3.10+),
and continues; nothing claims its coverage. `--analyzer required` exits 3 instead; `off` skips it.

### `torque-check.yml`

Optional, in the project folder (found from the first PATH up to the folder holding `sfdx-project.json`).
Every entry needs a reason.

```yaml
ignore:
  - rule: doc.help-text-missing
    reason: help text is maintained on the page layouts
  - path: force-app/main/default/classes/legacy/*
    reason: legacy code outside this engagement
  - rule: code.test-without-assert
    path: "*/AssertHelperTest.cls"
    reason: assertions live in the helper
```

### Not checked

Every report ends with the list. Always: grants made in Setup or as data records, assignments and what a
user can really see; flow activation in the org; whether a description is right rather than present. Also,
as they apply: unchanged components, components named in the scope with no file, types with no rules here,
lost page sections without a baseline, inherited problems without a baseline, and Code Analyzer's rules
when it did not run.

## `torque check result`

```
torque check result RESULT.json [--json]
```

One screen from a saved deploy, validate or dry-run result: status, deploy id, check-only or real, the
target org as the file states it (deploy results usually do not), counts of components created, changed,
unchanged, deleted and failed, each component failure (type, name, problem, line), each test failure (class,
method, message), coverage warnings, and the next step: for a failed check-only run, fix and validate again;
for a successful one, `torque check metadata --changed-from` the same file, then deploy; for a deploy still
queued, `sf project deploy report --job-id ID --json` to a file.

It reads what saved results look like in practice: a UTF-8 byte-order mark, UTF-16 from a Windows PowerShell
redirect, CLI warning lines before the JSON (stderr merged into the file), text after it, and the result with
or without the CLI's `{"status": ..., "result": ...}` envelope. A result with totals but no per-component
list prints the totals it states (in total, deployed, failed; in `--json` under `declared`, with the other
states `null`, never 0) and the `sf project deploy report` command that saves the full list;
`--changed-from` refuses such a file (exit 2), since the change cannot be told from it. Exit 0 when the file was read, whatever the
deploy outcome; exit 2 with the likely cause when it was not (no such file, empty, human-readable output, a
CLI error instead of a result, JSON that is not a deploy result). No org call.

## `torque check deployed`

Status: proven against a stubbed CLI and a mutation corpus. One live run on a Developer Edition org
(2026-11); the comparison rules changed after the live run and have not yet been re-run against a real org;
not yet run against a sandbox or a production org.

```
torque check deployed --target-org ALIAS (--changed-from RESULT.json | --source-dir DIR | --only Type:Name ...)
                      [--source-dir DIR] [--as-permission-set NAME ...] [--json]
```

After a deploy, read-only:

- **Same as submitted?** Retrieves the Created and Changed components of the result (or those named, or
  everything under `--source-dir`) with `sf project retrieve start --target-metadata-dir TMP --unzip` into a
  fresh private temporary folder, removed afterwards, and compares each with the submitted source. Types
  compared: custom objects, fields, validation rules, record types, flows, permission sets and groups, custom
  permissions, profiles, Lightning pages, layouts, custom metadata records, Apex classes and triggers. See
  [What the comparison establishes](#what-the-comparison-establishes).
- **Flows:** the active and the latest version through the flow definition (the route of
  `torque advisory flow`: Tooling `FlowDefinition` and `FlowDefinitionView`, plus the version numbers), and
  whether the latest version, the one a deploy creates, is the active one.
- **New fields:** which permission sets and profiles grant read or edit in the org (`FieldPermissions`
  rows), and whether each `--as-permission-set` does. A field with no rows is reported.
- **Validation rules:** active or not (Tooling `ValidationRule`).

Every call goes through Torque's launcher with an argument list (`sf.cmd` on Windows), names `--target-org`,
and only retrieves or queries. It never calls `sf org display` and prints no token. Exit 0 when the report
completes, whatever it found; 2 unusable input; 3 the org could not be read (for example an alias with no
CLI login). Not checked: permission set assignments and what a user really sees, and business acceptance.

### What the comparison establishes

| Status | Establishes | Does not establish |
|---|---|---|
| `same` | everything the file specifies is in the org unchanged, and the org copy has nothing else except default values, the properties Salesforce is known to add on save, or permission entries the file does not name | byte equality; that the component works; who can use it |
| `unexplained-additions` | everything the file specifies is in the org unchanged; the org copy also has top-level properties Torque cannot explain (listed: look at them) | whether those properties are harmless server defaults or someone's change |
| `different` | something submitted is missing or changed; or the org has content where a deploy replaces the definition; or the file revokes access the org still grants | which side is right |
| `partly-compared` | what could be compared is as submitted, but some access entries of a permission set or profile could not be (their components were not in the retrieve; the count is shown) | that those entries are in the org |
| `could-not-compare`, `not-in-org` | the pair could not be read, or the org reports no such component; for a flow whose latest version is not the active one, the retrieve's copy may be either version, so no verdict is given (what that copy showed is in the reason) | anything about its content |

The text summary counts `unexplained-additions` apart, never with `same`; so does `--json`
(`totals.unexplained-additions`, and per component `unexplained_additions`, `known_server_additions`,
`other_org_entries`).

Rules:

1. Replace by default. A deploy of a flow, field, rule, class, trigger, layout or page replaces its
   definition. In a flow, content only the org has is always a difference (a retained decision outcome
   means the latest version is not what was submitted). In other types, content only the org has inside an
   element the file has, or an extra member of a collection the file lists, is a difference; a top-level
   property the file does not have at all is `unexplained-additions` unless rule 2 explains it.
2. Known server additions, one table in `src/torque/checks/compare.py` (`SERVER_ADDITIONS`), each entry with
   the value and the retrieve it was seen in. Today: a custom object's action overrides of type Default,
   `compactLayoutAssignment` SYSTEM, the activity, bulk, feed, history, licensing, report, search, sharing and
   streaming switches, `externalSharingModel` Private, `deploymentStatus` Deployed, an empty `searchLayouts`
   and `visibility` Public, as the live run of 2026-11-14 returned them. A value not seen there is not
   explained.
3. Merge for permission sets and profiles. A deploy changes only the access entries the file names, so
   entries only the org has are expected (counted, shown as information). An entry the file names with every
   flag false (or a tab set to None or Hidden) is a revoke: met when the org has no such entry or an all-false
   one; `different` ("source revokes it; the org still grants ...") when the org entry grants anything. This
   covers field, object, class, tab, record type, custom permission and user permission entries. The org
   returns an access entry only for a component in the same retrieve, so the components a permission set or
   profile names are retrieved with it (a Task or Event field as the Activity field). An entry that still
   could not be compared is counted ("N entries not compared: their components were not in this retrieve")
   and the component is `partly-compared`, never `same`.
4. Code (classes, triggers): exact, after making line endings one kind and removing whitespace at the end of
   lines and of the file. A space inside a string or the line break after a `//` comment is content. The
   same function decides what changed for `torque check metadata --baseline` (also for the files of a
   Lightning component bundle).
5. Order: the Metadata API writes top-level elements in its own order, so that order is not compared; the
   order of same-tag siblings below it (picklist values, decision outcomes) is.

Also normalised, each with a corpus row: a leaf present on one side only with a default value (false, 0,
empty), and an element holding only such leaves; a number written with or without trailing zeros after the
point (`0` and `0.0`, `62` and `62.0`; `007` and `7` stay different); the case of an API name in a key or
reference (`Thing__c.kind__c` and `Thing__c.Kind__c`) and URL encoding in a record type's picklist value
names; leading and trailing whitespace of an XML value (a known limit: `" Approved "` and `"Approved"`
compare equal). On a layout, the order of sections and related lists is compared (it is what users see).
A standard object retrieved for the change is compared like a custom one.

Flows: each flow row says which version was compared (`compared_version`). When the latest version is the
active one, or none is active, that is the latest. When the org runs an older active version, a retrieve
returns one version without saying which, so the row is `could-not-compare`. The version queries filter on
the project's namespace (`NamespacePrefix = null` without one).

The mutation corpus (`tests/test_check_mutations.py`, 68 pairs, the same pairs through the baseline scope),
in short:

| Mutation | Flow | Object | Field | Rule | Permission set or profile | Class |
|---|---|---|---|---|---|---|
| removed in the file, the org keeps it | different | unexplained-additions | different (picklist value); unexplained-additions (property) | unexplained-additions | different when it was a grant the file now revokes | different |
| added in the org only | different | unexplained-additions; different inside an element | different | unexplained-additions | same (other entries); different when inside a named entry | different |
| a literal changed | different | different | different | different | different | different |
| whitespace inside a string | different | | different | different (formula) | | different |
| two outcomes or values reordered | different | same (top level) | different | same (top level) | same (top level) | |
| revoked in the file, the org keeps the grant | | | | | different | |
| revoked in the file, the org dropped it | | | | | same | |
| the server's defaults on a new object | | same | same | | | |
| an unknown property on an object | | unexplained-additions | | | | |
| line endings and trailing whitespace only | | | | | | same |

Layouts: a layout item or a section only the org has, and reordered sections, are `different`. A record type
picklist value only the org has is `different`. Numbers written `0` and `0.0`, and API names in another
case, are `same`.

What none of this establishes: a grant entry in a permission set is not a user's effective access
(assignments, groups, muting, license and sharing decide that: test as that user), and the comparison does
not show that the change does what the requirement asks.

## Build-only mode

In a build-only workspace the gate refuses every `torque` subcommand outside a short allowlist (`demo`,
`workflows`, `doctor`), so it refuses `torque check metadata` and `torque check result` too, although they
read only local files and the same mode allows `sf code-analyzer run` on paths outside `clients/`. Run the
check from your own terminal there, or in full or connected mode. `torque check deployed` reaches an org, so
build-only mode refusing it is intended.
