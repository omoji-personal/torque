# Analyzer rules and other tools

State described: Code Analyzer v5.16.0 and the tools as published on 2026-11-14. Rule lists
change with releases: **verify** every name here against the installed tool
(`sf code-analyzer rules --rule-selector flow --view detail`). Community tools are named as
such; none is a supported Salesforce product unless stated.

## 1. Code Analyzer engines

| Engine | Scans | Needs |
| --- | --- | --- |
| `pmd`, `cpd` | Apex (rules; copy-paste detection) | Java |
| `sfge` | Apex data flow | Java |
| `eslint`, `retire-js` | Lightning web components and JavaScript; known-vulnerable libraries | Node |
| `regex` | Any text, with patterns declared in `code-analyzer.yml` | Nothing extra |
| `flow` | Flow files (`*.flow-meta.xml`) | Python 3.10 or later |
| `apexguru` | Apex, using runtime insight | A connected org |

Source: the analyzer's repository and documentation (**verify**). Commands:
`sf code-analyzer run`, `sf code-analyzer rules`, `sf code-analyzer config`.

## 2. The flow engine's rules

Fifteen rules at v5.16.0 (**verify**; list read from a community page that prints the rule
table):

| Rule | In a default run | Related build rule |
| --- | --- | --- |
| `DbInLoop` | Yes | Nothing that queries or writes inside a loop |
| `HardcodedId` | Yes | No hard-coded Ids |
| `TriggerEntryCriteria` | Yes | Entry conditions on record-triggered flows |
| `SameRecordUpdate` | Yes | Same-record changes belong before save |
| `CyclicSubflow` | Yes | No circular subflow calls |
| `DefaultCopy` | Yes | No default or copied names |
| `TriggerCallout`, `TriggerWaitEvent` | Yes | Callouts and waits in record-triggered flows |
| `MissingNextValueConnector` | Yes | Loop wiring |
| `PreventPassingUserDataIntoElementWithoutSharing`, `PreventPassingUserDataIntoElementWithSharing` | Yes | Running context |
| `MissingFaultHandler` (severity 2) | No | A fault path on every data element and action |
| `MissingDescription` (severity 4) | No | A description on the flow and its elements |
| `UnreachableElement`, `UnusedResource` | No | No leftovers |

The four marked "No" are not tagged `Recommended`: a default run skips them. Use
`--rule-selector flow`. Read each rule's own description before you rely on what it tests:
the "related build rule" column is a guide, not the rule's definition.

## 3. Which tool checks what

| Check | Code Analyzer `flow` | Lightning Flow Scanner | sfdx-hardis |
| --- | --- | --- | --- |
| Flow or element description missing | `MissingDescription` (not in a default run) | `missing-flow-description`, `missing-metadata-description` | No |
| Fault path missing | `MissingFaultHandler` (not in a default run) | `missing-fault-path` | No |
| DML, query or action in a loop | `DbInLoop` | `dml-in-loop`, `soql-in-loop`, `action-call-in-loop` | No |
| Hard-coded Id, URL, secret | `HardcodedId` | `hardcoded-id`, `hardcoded-url`, `hardcoded-secret` | No |
| Entry conditions missing | `TriggerEntryCriteria` | `missing-record-trigger-filter` | No |
| Trigger order unset | No | `unspecified-trigger-order` | No |
| System context without sharing | `PreventPassingUserDataIntoElementWithoutSharing` | `unsafe-running-context` | No |
| Old API version | No | `invalid-api-version` | `hardis:project:audit:apiversion` |
| Naming, default copy names | `DefaultCopy` | `invalid-naming-convention`, `unclear-api-naming` | No |
| Unused or unreachable parts | `UnusedResource`, `UnreachableElement` | `unused-variable`, `unreachable-element` | `hardis:lint:unusedmetadatas` |
| Null handling after a Get | No | `missing-null-handler` | No |
| Process Builder present | No | `process-builder-usage` | No |
| Inactive flows or metadata | No | `inactive-flow` | `hardis:lint:metadatastatus` |
| Custom field description missing | No | No | `hardis:lint:missingattributes` |
| Flow documentation output | No | `sf flow doc` | `hardis:doc:flow2markdown` |

No public tool was found for: descriptions on objects, permission sets, validation rules and
custom metadata types; the field-access check for new fields; Lightning page activation; a
namespace guard; profile edits in a change. Those are the `[check]` rules in skills
`salesforce-build` and `salesforce-flow`.

## 4. The tools

- **Lightning Flow Scanner** (community, MIT). `sf plugins install lightning-flow-scanner`;
  `sf flow:scan` (with `--sarif` or `--csv`); configuration in `.flow-scanner.yml` [LFS].
- **sfdx-hardis** (community, AGPL-3.0, a copyleft licence: run it as an external command;
  copying its code into a project under another licence needs review).
  `sf plugins install sfdx-hardis`; `sf hardis:lint:missingattributes` lists custom fields
  with no description and writes a CSV [HARDIS].
- **Org Check** (Salesforce Labs, MIT, not a supported product). It needs an org connection
  and scores technical debt per component; it does not read local files [ORGCHECK].
  Salesforce points admins to it now that Salesforce Optimizer is being retired
  (**verify**).

Installing a plugin changes the consultant's machine: ask first, and prefer what is already
installed.

## 5. Custom rules

For a house rule no engine has, the `regex` engine takes patterns declared in
`code-analyzer.yml` (**verify**). Keep such rules few, each with the reason beside it. A rule
that inspects metadata structure (for example "every new field has a field permission")
belongs in a metadata check, not in a text pattern.

## 6. What Salesforce's own agent skills require

Salesforce publishes agent skills in `forcedotcom/sf-skills`. Its Apex skill makes an agent
run the analyzer on changed classes, fix the top severities and re-run until clean, and put
the analyzer and test output in its final report [SK-APEX]. Its flow skill generates flows
only through Salesforce's own metadata tool and states no rules on descriptions, fault paths
or naming [SK-FLOW], which is why skill `salesforce-flow` carries them. Those skills "may be
renamed, restructured, or removed between releases" [SF-SKILLS]: read the installed version.

## Sources

- Code Analyzer repository and releases: https://github.com/forcedotcom/code-analyzer
- Flow rule table as printed by a community linter page for 5.16.0: https://megalinter.io/latest/descriptors/salesforce_code_analyzer_flow/
- **verify** items: flow rules (https://developer.salesforce.com/docs/platform/salesforce-code-analyzer/guide/rules-flow.html),
  flow engine (https://developer.salesforce.com/docs/platform/salesforce-code-analyzer/guide/engine-flow.html),
  regex engine (https://developer.salesforce.com/docs/platform/salesforce-code-analyzer/guide/engine-regex.html).
- [LFS] https://github.com/Flow-Scanner/lightning-flow-scanner
- [HARDIS] https://github.com/hardisgroupcom/sfdx-hardis and https://sfdx-hardis.cloudity.com/hardis/lint/missingattributes/
- [ORGCHECK] https://github.com/SalesforceLabs/OrgCheck
- [SK-APEX] https://github.com/forcedotcom/sf-skills/blob/main/skills/platform-apex-generate/SKILL.md
- [SK-FLOW] https://github.com/forcedotcom/sf-skills/blob/main/skills/automation-flow-generate/SKILL.md
- [SF-SKILLS] https://github.com/forcedotcom/sf-skills
