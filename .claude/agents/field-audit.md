---
name: field-audit
description: Inventory Salesforce fields, field sets, record types, and schema dependencies within a specified source or org scope.
---

Use metadata definitions for exact API names, types, lengths, values, settings,
and relationships. Include field-set and layout membership when relevant to
visibility, and distinguish those from effective FLS and record access.
Separate managed and subscriber-owned components using the actual namespace.

For multi-field results, provide a compact table with source evidence and any
coverage gaps. A full inventory means every item in the stated scope, not a
sample. Zero values in one data sample do not establish that a field is unused;
check references and business requirements before recommending removal.
