# Skills

Torque packages portable skills for the assistant. `torque workspace init` copies them to
`.claude/skills/` and `.agents/skills/` in the private workspace, and `torque workspace
upgrade` updates them while keeping local edits. Skills are guidance, not workflows: they do
not appear in `torque workflows list`.

| Skill | Use it for |
| --- | --- |
| `salesforce-build` | Any change to configuration: objects and fields, validation rules, access, pages, list views, reports and dashboards, custom metadata, Apex and components, connectors. The standard for every build, with a check per rule |
| `salesforce-flow` | Building, changing, reviewing or debugging any flow, including activation and versions on deploy |
| `salesforce-delivery` | The work around the build, by phase: taking over an org, discovery, design and decision records, sandboxes, testing and UAT, data migration, release, hand-over, support, estimates and scope |
| `salesforce-architecture-review` | Reviewing a design or an org: a method, the five Well-Architected pillars, forty rules by phase, forty anti-patterns with how to detect each |
| `salesforce-soql-review` | Writing, running or reviewing SOQL: describe first, which API serves which object, restrictions that look like syntax errors, selectivity |
| `salesforce-code-analyzer-review` | Running and reading Salesforce Code Analyzer v5, including the flow rules a default run skips, and the other existing tools to reuse |
| `salesforce-npsp` | NPSP orgs with the Program Management Module and Outbound Funds: data model, households, TDTM, rollups, recurring donations, payments, allocations, gift entry, and gotchas with org checks |
| `salesforce-nonprofit-cloud` | Nonprofit Cloud: Fundraising, Program and Case Management, Grantmaking, Volunteer and Outcome Management, Person Accounts, Data Processing Engine rollups, licences, and NPSP to Nonprofit Cloud migration |

## How these load

- A host lists each skill's `name` and `description` and loads the body when the task
  matches the description, or when the assistant is told to read it. Each description says
  what the skill covers and when to use it.
- The body (`SKILL.md`) is short: what to do first, the standard with a check per rule, and
  pointers. Depth is in `references/` files that load only when the body sends the
  assistant there. A path such as `salesforce-build/references/data-model.md` is relative
  to the skills folder.
- On-demand skills are easy to skip. Name the skill in the request ("read the
  `salesforce-flow` skill first") or in the workspace's standing instructions for the kinds
  of work you do most.

## How to read them

- A rule tagged `[check]` can be tested mechanically in the source files, and names the
  existing tool rule where one exists (Salesforce Code Analyzer, Lightning Flow Scanner,
  sfdx-hardis).
- Every file ends with its sources. A statement marked "verify" was not confirmed in a
  fetched primary source: check it in the org or the current documentation before relying
  on it. "field-reported" marks a lesson from real client work, usually seen in one org,
  with the check that proves it.
- The skills describe platform behavior as of Winter '27 (API 68.0). Installed versions
  differ: the skills tell the assistant to confirm settings, namespaces and API names in
  the org.
