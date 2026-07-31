---
name: audit-org
description: Read-only survey of a Salesforce org — objects, automations, permissions, recent changes. Use to orient before any work, or to answer "what's in this org."
---
# audit-org

**What this adds over ad-hoc queries:** a standard, read-only, no-surprises survey that
never writes. Runs a fixed battery — object counts, active flows/triggers, validation
rules, permission-set assignments, setup audit trail — and returns a structured summary.
Uses only SELECT SOQL and describe. Dispatch the `org-explorer` subagent for the heavy
reading so the main context stays clean.
