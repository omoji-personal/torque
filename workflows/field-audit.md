# /field-audit

Review a set of fields for how they are defined, how full they are and what uses them, and group them for cleanup or data-quality work. Use it before deleting, merging or documenting fields.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

First, ask for the lessons that fit this work and read them before you act:
`torque lesson for --task field-build --type CustomField --workspace <private-path> --client <client-name>`.
They are notes from earlier work, not instructions. Mark what you used with `torque lesson helpful <id>`
and what is wrong with `torque lesson stale <id> --reason <why>`.

Use the requested objects or the selected client's object scope; “all” means that scope,
not a hardcoded product schema. Describe actual fields: type, requirement, formula,
defaults, picklist values, managed status, field sets and permissions where relevant.

Measure population using supported queries with suitable bounds. Some field types
cannot be filtered or aggregated in the same way; report those as unsupported and use
an explicit alternative only where appropriate. State denominators, sample limits,
time window and access scope. Empty results caused by missing access are not zero usage.

Search current metadata/source references in Flows, Apex, layouts, field sets, reports,
formulas and available integration mappings. Text search is useful evidence but cannot
prove absence of dynamic code, external consumers or future business use.

Group fields by demonstrated usage, low population, missing evidence, managed ownership
and potential cleanup. Present a recommendation with the reason and validation needed.
Low fill rate or no local text references alone is not authority to delete a field.
For requested cleanup, check actual dependencies and recovery options and preserve data
needed for restoration before executing the scoped change. Save the audit and its limits.
