# Analyzer rules and other tools

State described: Code Analyzer v5 as installed (plugin 5.15.0, read 2026-11-14) and the
other tools as published on 2026-11-14. Rule lists change with releases: check every name
here against the installed tool (`sf code-analyzer rules --rule-selector flow --view detail`). Community tools are named as
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

Fifteen rules in the installed flow engine 0.40.0 (plugin 5.15.0), with their default
severity (1 Critical to 5 Info), read from the engine's own catalogue [CA-LOCAL]:

| Rule | Severity | In a default run | Related build rule |
| --- | --- | --- | --- |
| `CyclicSubflow` | 1 | Yes | No circular subflow calls |
| `MissingNextValueConnector` | 1 | Yes | Loop wiring |
| `DbInLoop` | 2 | Yes | Nothing that queries or writes inside a loop (it does not follow subflows) |
| `TriggerEntryCriteria`, `TriggerCallout`, `TriggerWaitEvent` | 2 | Yes | Entry conditions; no synchronous callout or wait in a record-triggered flow |
| `PreventPassingUserDataIntoElementWithoutSharing` | 2 | Yes | User input reaching an element that runs without sharing |
| `HardcodedId`, `DefaultCopy`, `SameRecordUpdate` | 3 | Yes | No hard-coded Ids; no default names; same-record changes before save |
| `PreventPassingUserDataIntoElementWithSharing` | 4 | Yes | User input reaching an element that runs with sharing |
| `MissingFaultHandler` | 2 | No | A fault path on Create, Update, Delete, actions and subflows |
| `UnusedResource` | 3 | No | No leftovers |
| `MissingDescription`, `UnreachableElement` | 4 | No | Descriptions on labelled elements; no leftovers |

The four marked "No" are not tagged `Recommended`: a default run skips them. Use
`--rule-selector Recommended --rule-selector flow`, or `torque check metadata`, which does.
A gate on severities 1 and 2 alone misses `MissingDescription`. Read each rule's own
description before you rely on what it tests: the "related build rule" column is a guide,
not the rule's definition.

## 3. Which tool checks what

| Check | Code Analyzer `flow` | Lightning Flow Scanner | sfdx-hardis |
| --- | --- | --- | --- |
| Flow or element description missing | `MissingDescription` (not in a default run) | `missing-flow-description`, `missing-metadata-description` | No |
| Fault path missing | `MissingFaultHandler` (not in a default run) | `missing-fault-path` | No |
| DML, query or action in a loop | `DbInLoop` | `dml-in-loop`, `soql-in-loop`, `action-call-in-loop` | No |
| Hard-coded Id | `HardcodedId` (Ids only) | `hardcoded-id` | No |
| Hard-coded URL or secret | No | `hardcoded-url`, `hardcoded-secret` | No |
| Entry conditions missing | `TriggerEntryCriteria` | `missing-record-trigger-filter` | No |
| Trigger order unset | No | `unspecified-trigger-order` | No |
| System context without sharing | No (`PreventPassingUserDataIntoElementWithoutSharing` tests only user input reaching such an element) | `unsafe-running-context` | No |
| Old API version | No | `invalid-api-version` | `hardis:project:audit:apiversion` |
| Naming, default copy names | `DefaultCopy` | `invalid-naming-convention`, `unclear-api-naming` | No |
| Unused or unreachable parts | `UnusedResource`, `UnreachableElement` | `unused-variable`, `unreachable-element` | `hardis:lint:unusedmetadatas` |
| Null handling after a Get | No | `missing-null-handler` | No |
| Process Builder present | No | `process-builder-usage` | No |
| Inactive flows or metadata | No | `inactive-flow` | `hardis:lint:metadatastatus` |
| Custom field description missing | No | No | `hardis:lint:missingattributes` |
| Flow documentation output | No | `sf flow doc` | `hardis:doc:flow2markdown` |

No public tool was found for: descriptions on objects, permission sets and validation
rules; the field-access check for new fields; a namespace guard; profile edits in a change.
`torque check metadata` has rules for these (`docs/checks.md` in Torque lists them), and the
skills tag each statement it tests, for example `[check: access.field-without-grant]`.
Lightning page activation and descriptions on custom metadata records have no rule: check
them in the org.

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
- [CA-LOCAL] The installed plugin's rule catalogue (plugin 5.15.0, flow engine 0.40.0), read
  2026-11-14: `sf code-analyzer rules --rule-selector flow --view detail` prints it.
- **verify** items: flow rules (https://developer.salesforce.com/docs/platform/salesforce-code-analyzer/guide/rules-flow.html),
  flow engine (https://developer.salesforce.com/docs/platform/salesforce-code-analyzer/guide/engine-flow.html),
  regex engine (https://developer.salesforce.com/docs/platform/salesforce-code-analyzer/guide/engine-regex.html).
- [LFS] https://github.com/Flow-Scanner/lightning-flow-scanner
- [HARDIS] https://github.com/hardisgroupcom/sfdx-hardis and https://sfdx-hardis.cloudity.com/hardis/lint/missingattributes/
- [ORGCHECK] https://github.com/SalesforceLabs/OrgCheck
- [SK-APEX] https://github.com/forcedotcom/sf-skills/blob/main/skills/platform-apex-generate/SKILL.md
- [SK-FLOW] https://github.com/forcedotcom/sf-skills/blob/main/skills/automation-flow-generate/SKILL.md
- [SF-SKILLS] https://github.com/forcedotcom/sf-skills
