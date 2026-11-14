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
| PATH that is a file | those files | The project folder around them is read for context (field definitions, grants elsewhere). |
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
| `access.field-without-grant` | error | a new custom field that takes field-level security (not required, not master-detail, not on a custom metadata type, platform event or custom setting) with no readable or editable `fieldPermissions` entry in a permission set in the change | grants made in Setup or as data records are invisible; a custom setting is recognised only when its object file is read; with no scope every field counts as possibly new |
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
| `flow.reruns-on-every-update` | warning | an update-triggered flow with entry conditions and side effects (Create Records, actions, subflows, scheduled paths) that does not require the record to change to meet them and never reads `$Record__Prior`, ISCHANGED, PRIORVALUE or ISNEW | a decision that checks prior values another way is not recognised |
| `flow.after-save-same-record` | note | an after-save flow whose only data change updates the triggering record | |
| `flow.hard-coded-id` | warning | a 15 or 18 character record Id in a flow string value, formula, filter formula or text template | the Id itself is not printed, only its key prefix |
| `flow.api-version-behind` | note | apiVersion below the project's `sourceApiVersion` or the newest flow in scope; error when the change lowers it below its baseline | |
| `flow.screen-action-without-trigger` | warning | a screen action no trigger in its screen runs (it deploys and never runs); note for a trigger event name other than `flow__screeninit` or `flow__screenfieldattributechange` | event names other than those two were not observed and may be valid |
| `flow.default-names` | note | default or copied names and labels (Copy_1_of_..., Decision_1, myDecision, New Outcome) | |
| `flow.status-not-active` | note | the file asks for Draft or Obsolete | activation in the org is `torque check deployed`'s |
| `flow.active-in-source` | note | the file asks for Active (a sandbox activates on deploy; production only with "Deploy processes and flows as active") | |
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
as Torque's finding with the analyzer in `confirmed_by`:

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
or without the CLI's `{"status": ..., "result": ...}` envelope. Exit 0 when the file was read, whatever the
deploy outcome; exit 2 with the likely cause when it was not (no such file, empty, human-readable output, a
CLI error instead of a result, JSON that is not a deploy result). No org call.

## Build-only mode

In a build-only workspace the gate refuses every `torque` subcommand outside a short allowlist (`demo`,
`workflows`, `doctor`), so it refuses `torque check metadata` and `torque check result` too, although they
read only local files and the same mode allows `sf code-analyzer run` on paths outside `clients/`. Run the
check from your own terminal there, or in full or connected mode.
