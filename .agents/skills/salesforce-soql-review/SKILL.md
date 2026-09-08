---
name: salesforce-soql-review
description: Draft, explain, optimize, or review Salesforce SELECT SOQL; verify fields and relationships and interpret targeted query evidence.
---

# Salesforce SOQL review

Define the data question and expected result shape. Use available metadata to
verify object, field, and relationship names; label assumptions when schema is
unavailable. Prefer the fewest fields and rows that answer the question, with a
defensible limit or aggregation. Explain selectivity and scale concerns when
they matter.

Run a query when execution is within the user's request, using an explicit org
and the existing Salesforce CLI or connector. Do not switch targets because
another org is already authenticated. Keep sensitive result values out of
shared documentation; persist raw evidence only when the task needs it in the
selected private workspace.

Clearly distinguish a drafted query, schema verification, and actual execution.
Explain incomplete results, API errors, and unknown schema facts. This skill
does not add a separate approval flow to ordinary investigation.
