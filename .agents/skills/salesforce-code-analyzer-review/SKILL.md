---
name: salesforce-code-analyzer-review
description: Run and read Salesforce Code Analyzer version 5 on Apex, Lightning web components and flows. Use it before deploying changed Apex, component or flow files, when asked for a static review, when choosing which engines and rules to run, and when interpreting analyzer findings or a failed analyzer run. Covers the commands, the rule selector that includes the flow rules a default run skips, scoping to changed files, reading severities, telling a defect from a configuration failure, what no analyzer covers, and the other existing tools to reuse instead of building checks.
---

# Salesforce Code Analyzer review

Reuse the analyzer; never rebuild its rules. State described: Code Analyzer v5 as installed
(plugin 5.15.0, flow engine 0.40.0, read 2026-11-14; v5.16.0 was the latest release), Winter
'27. Rule sets change with each release: list them from the installed version before you
rely on a rule name. Version 4 and its `sf scanner` commands are retired (**verify**).

Marks: **verify** = not confirmed in a fetched primary source: verify with the command
given or the current documentation. field-reported = seen in real client work.

## Run it

With Torque, run `torque check metadata PATH --changed-from dry-run.json`: it runs the
analyzer on the changed flow, Apex and component files with the right selectors, merges
its findings with Torque's own and says when the analyzer did not run. Without it, one
command:

```sh
sf code-analyzer run --workspace . --target PATH_TO_CHANGED_FILES --rule-selector Recommended --rule-selector flow --output-file analyzer.json
```

1. **Check it is there.** `sf code-analyzer run --help`. Engines need their runtimes: Python
   3.10 or later for the flow engine [CA-LOCAL]; Java for the Apex engines and Node for the
   component engines (**verify**). An engine that could not start reports nothing: that is not a clean
   result.
2. **Scope to what changed.** Give `--target` the changed files or folders (from the diff of
   your change), not the whole project and not vendor or generated files. Keep
   `--workspace` the project folder: some engines need the whole code base [CA-HELP].
3. **Pick the rules.** The default selector is `Recommended`, and a selector you pass
   replaces it. Repeating `--rule-selector` (or a comma) joins selectors; a colon narrows
   one, as in `eslint:Recommended` [CA-HELP]. `MissingFaultHandler`, `MissingDescription`,
   `UnreachableElement` and `UnusedResource` are not tagged `Recommended` [CA-LOCAL], so add
   `--rule-selector flow` whenever flows are in scope. `-o` is `--target-org` (for remote
   engines); the output file is `-f`, `--output-file` [CA-HELP].
4. **Write the result to a file.** The format follows the extension: `.csv`, `.html`,
   `.json`, `.sarif`, `.xml`; the folder must exist [CA-HELP]. Read the file. Do not pipe
   the command through a filter: an exit code alone is not proof of a clean run, and a pipe
   hides a failure (field-reported). `--severity-threshold 2` makes the command fail on a
   finding of severity 2 or worse.
5. **Keep the result file** with the change record.

## Read the result

| It says | It means | Do |
| --- | --- | --- |
| Severity 1 (Critical) or 2 (High) | The top of the scale, which runs 1 Critical, 2 High, 3 Moderate, 4 Low, 5 Info [CA-LOCAL] | Fix before deploy and re-run. Salesforce's Apex skill says the same with older names ("`sev0`, `sev1`, and `sev2`") [SK-APEX]; v5 has no severity 0 |
| `MissingFaultHandler` (2) or `MissingDescription` (4) on a flow you created or changed | A flow rule outside `Recommended` | Fix it, whatever its severity: the flow standard requires both (skill `salesforce-flow`) |
| Other findings at 3 to 5 | Maintainability and style | Fix what the change touched; list the rest |
| An engine error or a missing runtime | Part of the scan did not run | Fix the setup or state exactly which engine and files were not scanned |
| No violations | The rules that ran found nothing in the files you named | Say which selector, which engines and which files |
| A finding you judge wrong | A false positive | Suppress it only with a written reason beside it |

For each finding report: file and line, rule and engine, what could go wrong in practice,
the fix. Group duplicates of one defect. Then name the runtime checks the analyzer cannot
replace: a bulk test, a test as the intended user, the active flow version.

## What it does not cover

A clean scan says nothing about these; check them another way:

| Not covered | Check |
| --- | --- |
| Descriptions and help text on fields, objects, validation rules and permission sets | `torque check metadata`: `[check: doc.description-missing, doc.help-text-missing, doc.too-long]`; `salesforce-build/references/documentation-and-naming.md` |
| Field access shipped with a new field | `torque check metadata`: `[check: access.field-without-grant]`; `salesforce-build/references/access-and-sharing.md` |
| A Lightning page that is deployed but not activated or assigned | `salesforce-build/references/pages-and-reports.md` |
| A managed-package component in the change; a profile edit | Read the manifest |
| Whether a deployment, an access model or a user's task works | Tests and a run as the intended user |

## Other existing tools

Use what is installed before writing a check. Do not install tools only for one review.
Details and the rule lists: `references/rules-and-tools.md`.

| Tool | Reads | Good for |
| --- | --- | --- |
| Code Analyzer `flow` engine (Salesforce) | Local flow files | Loops, hard-coded Ids, entry criteria, fault handlers, descriptions |
| Lightning Flow Scanner (community, MIT) | Local flow files | Trigger order, null handling, API version, naming, more flow rules |
| sfdx-hardis (community, AGPL-3.0: run it as an external command) | Local project | Custom fields without a description, inactive and unused metadata, API versions |
| Org Check (Salesforce Labs, not a supported product) | A connected org | Technical-debt scoring across an org |

## After the scan

If fixes were asked for, make them, re-run only the affected checks, and report what changed.
A review alone is not a request to deploy. Use the scope and authorization you were given
for anything that follows.

## References

- `references/rules-and-tools.md`: the flow rules, which checks each tool covers, custom
  rules, what Salesforce's own agent skills require.

A path that starts with another skill's name, such as
`salesforce-build/references/apex-and-lwc.md`, is relative to the skills folder that holds
this skill.

## Sources

- Code Analyzer releases (v5.16.0 marked latest): https://github.com/forcedotcom/code-analyzer/releases
- [CA-HELP] `sf code-analyzer run --help`, plugin 5.15.0, read 2026-11-14 (flags, selector
  syntax, output formats, severity threshold).
- [CA-LOCAL] The installed plugin's own rule catalogue (flow engine 0.40.0, PMD engine
  0.45.0, engine API severity scale), read 2026-11-14: list it with
  `sf code-analyzer rules --rule-selector all --view detail`.
- [SK-APEX] https://github.com/forcedotcom/sf-skills/blob/main/skills/platform-apex-generate/SKILL.md
- Salesforce's analyzer skill for agents: https://github.com/forcedotcom/sf-skills/blob/main/skills/dx-code-analyzer-run/SKILL.md
- **verify** items: command reference (https://developer.salesforce.com/docs/platform/salesforce-cli-reference/guide/cli_reference_code-analyzer_run.html),
  analyzing code (https://developer.salesforce.com/docs/platform/salesforce-code-analyzer/guide/analyze.html),
  flow engine (https://developer.salesforce.com/docs/platform/salesforce-code-analyzer/guide/engine-flow.html).
- field-reported: lessons from real client work with Torque (a classification of 273 failed
  Salesforce CLI calls). Not re-tested for this release.
