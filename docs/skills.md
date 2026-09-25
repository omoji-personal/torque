# Skills

Torque packages portable skills for the assistant. `torque workspace init` copies them to
`.claude/skills/` and `.agents/skills/` in the private workspace, and `torque workspace
upgrade` updates them while keeping local edits. Each skill is a `SKILL.md` with `name`
and `description` frontmatter; the assistant loads it when a task matches the
description. Some skills keep detail in a `references/` folder that the skill points to.
Skills are guidance, not workflows: they do not appear in `torque workflows list`.

| Skill | Use it for |
| --- | --- |
| `salesforce-architecture-review` | Reviewing a data model, automation, integration or access design against outcomes and maintainability |
| `salesforce-code-analyzer-review` | Scoping and interpreting Salesforce Code Analyzer results for Apex, Lightning, Flow and metadata |
| `salesforce-soql-review` | Drafting, explaining, optimizing or reviewing SELECT SOQL |
| `salesforce-npsp` | NPSP orgs with the Program Management Module and Outbound Funds: data model, households, TDTM, customizable rollups, enhanced recurring donations, payments, allocations, soft credits, gift entry, PMM attendance and rollup gates, flows on NPSP objects, and gotchas with org checks |
| `salesforce-nonprofit-cloud` | Nonprofit Cloud: Fundraising, Program and Case Management, Grantmaking, Volunteer and Outcome Management, Person Accounts and households, Data Processing Engine rollups, licenses, metadata versus UI setup, gotchas, and NPSP to Nonprofit Cloud migration |

The two nonprofit skills describe platform behavior as of Winter '27 (API 68.0) and cite
Salesforce documentation and the NPSP source. Installed versions differ: the skills tell
the assistant to confirm settings, namespaces and API names in the org, and they mark
claims that still need confirmation as "verify".
