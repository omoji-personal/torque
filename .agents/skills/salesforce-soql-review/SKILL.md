---
name: salesforce-soql-review
description: Draft, run, explain, optimize or review Salesforce SOQL and the sf data query commands that carry it. Use it before the first query on an object or setup entity you have not described in this session, when a query fails with an unknown field, an unsupported object or a restriction message, when deciding between the standard and the Tooling API, and when a result will be quoted to a client. Covers describe before you query, never guessing a name twice, entities that refuse OR, grouping, counting or filtering, reading the whole error, selectivity on large objects, and query and command-line traps with a check each.
---

# Salesforce SOQL review

Describe before you query, and look a name up instead of guessing it twice. State described:
Winter '27 (API 68.0), checked 2026-11-14.

Marks: **verify** = not confirmed in a fetched primary source: verify in the org or the
current documentation. field-reported = from real client work: a classification of 273
failed Salesforce CLI calls, 150 of them queries (one consultant, mostly sandboxes). [ADV] =
Torque advisory catalogue, with its own confidence label per entry.

## Before the first query on an object

1. **Name the org**: `--target-org ALIAS` on every command. Do not switch to another org
   because it is already signed in.
2. **Look the names up.** `sf sobject describe --sobject Object__c --target-org ALIAS --json`
   gives each field's name, type, `filterable`, `groupable`, `nillable`, and the
   `relationshipName` of lookups and child relationships. A relationship name is not the
   lookup's label. Do this for setup and metadata entities too: their fields are not the
   ones a custom object would have.
3. **Confirm the object exists** before you name it from memory: `sf sobject list
   --target-org ALIAS`. An object from another product generation is absent (Nonprofit
   Cloud objects in an NPSP org, and the reverse); a managed field's prefix is not always
   its object's.
4. **Choose the API per query**, not per session (table below).
5. **Ask for the least that answers the question**: the fields you need, a count or a
   GROUP BY where a number is the answer, a LIMIT where rows are.

field-reported: 52 queries failed on an unknown field or relationship name; after such a
failure the agent guessed again 37 times out of 51 and looked the name up 8 times. Only 2 of
314 first queries on setup entities were preceded by a check. After "No such column" or
"Didn't understand relationship", describe. Do not guess again.

## Standard API or Tooling API

| Entity | API | Evidence |
| --- | --- | --- |
| `FlowDefinitionView`, `SetupAuditTrail`, `FieldPermissions`, `ObjectPermissions`, `PermissionSet`, `PermissionSetAssignment` | Standard: the Tooling flag fails | [ADV `flowdefinitionview-is-standard-api`, `tooling-api-refuses-permission-objects`]; field-reported |
| `FlowDefinition`, `ValidationRule` and `WorkflowRule` metadata, `ApexTrigger`, `CustomField`, layouts, installed package versions | Tooling: add `--use-tooling-api` | [ADV]; field-reported |
| `FieldDefinition`, `EntityDefinition` | Answered on both in the field record (the catalogue says Tooling only): try the standard API first | field-reported, 48 queries |
| Any custom object | Standard. Never the Tooling flag | field-reported, 8 failures |

When a query fails with "sObject type X is not supported", try the other API once and note
which one answered.

## Restrictions that look like syntax errors

| You are about to | It fails with | Do instead | Evidence |
| --- | --- | --- | --- |
| Put OR in a WHERE on `FieldDefinition`, `FlowDefinitionView` or a custom metadata type | "Disjunctions not supported" | IN on one field, one query per branch, or filter the rows yourself | field-reported, 13 |
| GROUP BY a formula, percent, label or raw date and time field | "field X can not be grouped" | Check `groupable` in describe; group by a date function, or count the rows yourself | field-reported, 13 |
| COUNT(Id) with GROUP BY on some setup and custom metadata entities | "does not support aggregate operator COUNT" | COUNT() without grouping, or count client-side | field-reported, 6 |
| Filter on a long text area or some setup fields | "field X can not be filtered" | Check `filterable`; filter on another field, then in code | field-reported, 7; [ADV `history-fields-unfilterable`] |
| Filter permission rows with LIKE on `Field` | "invalid operator on id field" | `=` or `IN` with the full `Object.Field`, plus `SobjectType` | field-reported, 4 |
| Use a sub-select on the same object, or with a left side that is not an Id | MALFORMED_QUERY | Fetch the Ids first and bind them, or walk level by level | [ADV `self-join-forbidden`]; field-reported, 3 |
| Query some setup entities without their key filter | "Implementation restriction" | Add the `Id = '...'` or key filter the message asks for | field-reported, 3 |
| Alias an aggregate `first` or `last`, or use a string function in SELECT | MALFORMED_QUERY | Another alias; SOQL has no string functions | field-reported, 3 |
| Put `ALL ROWS` in the text of `sf data query` | "unexpected token: 'ALL'" | Drop it and pass `--all-rows` | [ADV `all-rows-is-a-cli-flag`] |
| Query a change event or platform event | "does not support query" | There are no rows: not applicable, not unknown | [ADV `event-channels-do-not-support-query`] |

An error means the query did not run. Never read it as "no rows".

## Read the whole error

- Read the full message with its row, column and caret line. Do not pipe a query through a
  filter or send its errors to nowhere: 14 failures in the field record cost a second run
  because the message was hidden or cut.
- Write `--json` results to a file and read the file. With `--output-file`, also pass
  `--result-format`.
- `--result-format csv` printed blanks for aliased aggregates (field-reported): use JSON.
- If you wrap queries in a redacting layer, pass the SOQL error text through: it holds no
  record data.

## Before a result goes anywhere

- Say which you have: a drafted query, a schema check, or an executed query with its org,
  date and row count.
- Zero rows needs a second look: `=` on text ignores case, a checkbox needs `= true`, a
  lowercased 18-character Id matches nothing (field-reported), and a permission set held
  through a group has no assignment row of its own [ADV `psg-assignment-names-the-group`].
- A query straight after a save does not show asynchronous effects
  [ADV `post-commit-work-is-invisible-to-the-caller`].
- Keep record values out of notes and shared documents. For a study of production data,
  count and group; put raw rows only in the private workspace, and only when the task needs
  them.
- Record-count commands are approximate. Use `SELECT COUNT()` for a number you will quote.

## On large objects

A filter helps only if it is selective and on an indexed field. Details, thresholds and how
to read a query plan: `references/query-and-cli-traps.md`.

## References

- `references/query-and-cli-traps.md`: every trap with its check, selectivity, counting
  hierarchies, handing names to sub-agents.

## Sources

- [ADV] Torque advisory catalogue: `torque advisory notes --command "sf data query"`; entry
  ids inline, each with its own label (verified live, documented or practitioner).
- field-reported: a classification of 273 failed Salesforce CLI calls and the working notes
  of the same period (one consultant, about a dozen clients, mostly sandboxes). Causes are
  the analyst's reading of the error text. Not re-tested for this release.
- **verify** items: SOQL and SOSL reference, query selectivity (https://developer.salesforce.com/docs/platform/salesforce-large-data-volumes-bp/guide/ldv-deployments-infrastructure-salesforce-query-optimizer.html).
