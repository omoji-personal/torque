# Query and command-line traps, each with a check

State described: Winter '27 (API 68.0), checked 2026-11-14. Evidence is named on each item:
[ADV] (Torque advisory catalogue, with its own confidence label), field-reported (real
client work: 150 failed query calls classified, plus working notes; the cause is the
analyst's reading of the error text) or **verify** (not confirmed in a fetched primary
source). None was re-tested for this release: run the check.

## Names

1. **A field name you have not seen in a describe is a guess.** 40 queries failed on "No
   such column": 17 on setup and metadata entities (a plausible label, date or status field
   that the entity does not have), 11 on managed-package objects (wrong word form or wrong
   prefix), 7 on standard objects, 5 on local custom objects (field-reported). Check:
   describe, or read the object's local metadata folder, before the first query.
2. **After an unknown-name error, look it up.** A second guess was the usual response and
   usually failed again (field-reported). Check: the next command after the error is a
   describe or a `FieldDefinition` query, not another query.
3. **Relationship names come from describe.** The parent path uses the lookup's
   `relationshipName`; a child sub-select uses the child relationship name. Neither is the
   field's label (field-reported, 9). Check: both are in the describe output.
4. **A share object ends in `__Share`,** and an object of another product generation is
   absent in this org (field-reported, 6). Check: `sf sobject list --target-org ALIAS`.
5. **A new field with no field access fails as an unknown name** for a user who cannot see
   it, in a query and in anonymous Apex (field-reported; [ADV `fls-not-automatic`]). Check:
   `SELECT Field, Parent.Name FROM FieldPermissions WHERE Field = 'Object__c.Field__c'`.
6. **Names in the repository are not names in the org**: a namespace prefix shows in the
   packaging org too [ADV `namespace-in-packaging-org`]. Check: resolve against the org the
   query runs in.
7. **Give helpers the names.** Sub-agents repeated each other's failed queries: one GROUP BY
   failed in four helper contexts of one session (field-reported). Check: the brief to a
   helper lists the object and field names already confirmed and the restrictions already
   met.

## Which API, which object

8. **`FlowDefinitionView` is a standard object; `FlowDefinition` is a Tooling object**
   [ADV, verified live]. Check: a zero-row or "not supported" answer on either means try the
   other API.
9. **Permission objects are not served by the Tooling API**: `FieldPermissions`,
   `PermissionSetAssignment`, `PermissionSet` [ADV `tooling-api-refuses-permission-objects`].
   The same note says `FieldDefinition` and `EntityDefinition` exist only in the Tooling
   API; in the field record 48 queries on them succeeded on the standard API. Check: try the
   standard API first for those two and say which API answered.
10. **Never the Tooling flag on a custom object** (field-reported, 8 failures with the flag
    on a standard-API entity or custom object).
11. **History objects cannot be filtered on `NewValue` or `OldValue`** [ADV
    `history-fields-unfilterable`, verified live]. Check: select by parent Id and date, then
    filter the rows yourself.
12. **Some relationships can never be counted**: activity history, open activities, combined
    attachments and similar views appear as child relationships in describe and refuse a
    query [ADV `entity-types-that-refuse-count`]. Check: report them as not countable, not
    as zero and not as failed.
13. **Change events and platform events hold no rows to query** [ADV
    `event-channels-do-not-support-query`]. External objects are different: they hold real
    rows elsewhere and merely cannot be counted.

## Shape of the query

14. **OR is refused on some setup entities** (`FieldDefinition`, `FlowDefinitionView`,
    custom metadata types): "Disjunctions not supported" (field-reported, 13 in 5 sessions).
    Check: use IN on one field or one query per branch.
15. **A field must be groupable to GROUP BY it.** Formula, percent, combobox, label and raw
    date and time fields are not (field-reported, 13). Check: `groupable` in describe; group
    by a date function such as `CALENDAR_YEAR()`, or count rows yourself.
16. **A field must be filterable to appear in WHERE.** Long text areas and several setup
    fields are not (field-reported, 7). Check: `filterable` in describe.
17. **COUNT(Id) with GROUP BY is refused on some entities** (field-reported, 6). Check: use
    `COUNT()` without grouping, or fetch the rows and count.
18. **A sub-select cannot read the same object as the outer query,** and its left side must
    be an Id [ADV `self-join-forbidden`] (field-reported, 3). Check: fetch parent Ids first
    and bind them, or walk a hierarchy level by level.
19. **`first` and `last` are reserved** and fail as aggregate aliases; SOQL has no string
    functions in SELECT (field-reported, 3).
20. **`ALL ROWS` in the query text is rejected by the CLI**: pass `--all-rows` [ADV
    `all-rows-is-a-cli-flag`]. Check: `sf data query --all-rows --query "SELECT COUNT() FROM
    Account WHERE IsDeleted = true" --target-org ALIAS`.
21. **Text comparison ignores case, a checkbox needs `= true`, and a lowercased
    18-character Id matches nothing** (field-reported). Check: test the filter on a record
    you know.

## Reading results

22. **An error is not an empty result.** A malformed or refused query never ran. Check: the
    result has a row count, not only an absence of rows.
23. **Read the whole error.** A filter or a discarded error stream cut the message in 14
    failed calls and each cost a second run (field-reported). Check: the output shows the
    error code, the message and the caret line.
24. **`--output-file` needs `--result-format`** (field-reported, 3). JSON saved by Windows
    PowerShell can start with a byte-order mark: read it as UTF-8 with a signature.
25. **CSV output printed blanks for aliased aggregates** (field-reported). Check: use JSON
    for aggregates.
26. **A count for a permission set held through a group is zero** on a direct query [ADV
    `psg-assignment-names-the-group`]. Check: also query `PermissionSetGroupComponent`.
27. **A REST request by Id that answers "not found"** can mean the running user cannot see
    the record, or a path wrong for the API version (field-reported, 16, half never
    resolved). Check: query for the record first.
28. **Reading a result grid through page tools dropped short cells** in the Developer
    Console (field-reported). Check: use `COUNT(field)` per column.
29. **A low daily API allowance fails unrelated calls together** in a Developer Edition org
    [ADV `de-daily-api-limit-masquerades`]. Check: `sf org list limits --target-org ALIAS`.

## Large objects

All **verify** (Salesforce large-data-volume guidance, read as search extracts), except the
default index list, which Trailhead states [TH-LDV].

- A filter is selective when it returns a small share of the object's rows on an indexed
  field. Published thresholds: under 30% of the first million rows plus 15% of the rest for
  a standard index, and 10% plus 5% for a custom index, each with a cap.
- Indexed by default: Id, Name, owner, created date, system modstamp, lookup and
  master-detail fields, and custom fields marked External Id or Unique [TH-LDV].
- These defeat an index: negative operators, a leading wildcard in LIKE, comparisons with
  null, OR across fields that are not indexed. Rows in the Recycle Bin still count.
- Check before a query, a report or a list view goes live on a large object: the Query Plan
  tool in the Developer Console, or the REST query resource with `explain`.
- Skew: a parent with more than about 10,000 children, or an owner with more than about
  10,000 records, slows queries and causes locks [WA-RC].

## Safe execution

- One literal command per call, with `--target-org`. Put logic in a script file.
- Run queries read-only in production; use COUNT and GROUP BY for studies; keep record
  values out of notes.
- For a number that will be quoted, use `SELECT COUNT()`: `sf org list sobject
  record-counts` is approximate [PLUGIN-LIMITS].
- An unbounded query in Apex is a finding: no WHERE and no LIMIT.

## Sources

- [ADV] Torque advisory catalogue (`torque advisory notes --command "sf data query"`).
  `flowdefinitionview-is-standard-api`, `flow-retrieve-latest-only` and
  `history-fields-unfilterable` are marked verified live there; the others practitioner.
- [TH-LDV] https://trailhead.salesforce.com/content/learn/modules/large-data-volumes/conduct-data-queries-and-searches
- [WA-RC] https://architect.salesforce.com/docs/architect/well-architected/guide/resource-and-cost-optimization.html
- [PLUGIN-LIMITS] https://raw.githubusercontent.com/salesforcecli/plugin-limits/main/README.md
- **verify** items: query selectivity (https://developer.salesforce.com/docs/platform/salesforce-large-data-volumes-bp/guide/ldv-deployments-infrastructure-salesforce-query-optimizer.html,
  https://help.salesforce.com/s/articleView?id=000006044&type=1).
- field-reported: a classification of 273 failed Salesforce CLI calls (150 failed queries)
  and the working notes of the same period (one consultant, about a dozen clients, mostly
  sandboxes). Not re-tested for this release.
