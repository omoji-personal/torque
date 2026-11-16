# Skills

Torque packages portable skills for the assistant. `torque workspace init` copies them to
`.claude/skills/` and `.agents/skills/` in the private workspace, and `torque workspace
upgrade` updates them while keeping local edits. Skills are guidance, not workflows: they are
not in the recipe catalogue (`torque workflows list --json`); `torque workflows list` shows them
after the recipes, one line each.

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
- On-demand skills are easy to skip. The Torque block in the workspace `AGENTS.md` names the
  file to read for each kind of work (`.agents/skills/NAME/SKILL.md`, in every workspace), so
  a host with no skill mechanism follows it too. You can also name the skill in the request
  ("read the `salesforce-flow` skill first").
- `torque workflows` ends with the eight skills, one line each. `torque workflows show NAME`
  prints a skill and `torque workflows show NAME/REFERENCE` one of its references (for
  example `salesforce-flow/gotchas`), from the selected workspace's copy when `--workspace`
  names one, otherwise from the installed package.

## How to read them

- A rule tagged with a rule id, such as `[check: access.field-without-grant]`, is tested by
  that rule of `torque check metadata` (`docs/checks.md`); a test keeps every tag pointing at
  a rule that exists. "Code Analyzer: RULE" names an analyzer rule instead, and "by hand"
  says what to read where no tool checks it.
- Every file ends with its sources. A statement marked "verify" was not confirmed in a
  fetched primary source: check it in the org or the current documentation before relying
  on it. "field-reported" marks a lesson from real client work, usually seen in one org,
  with the check that proves it.
- The skills describe platform behavior as of Winter '27 (API 68.0). Installed versions
  differ: the skills tell the assistant to confirm settings, namespaces and API names in
  the org.
