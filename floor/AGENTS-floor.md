<!-- torque:floor:begin 1 -->
## Torque: how to work in this workspace

Torque writes this block and `torque workspace upgrade .` replaces it; put firm instructions outside the markers.

### Start and finish
- Work only for the client or initiative the consultant named. Start with `torque context --workspace . --client NAME`: it prints that client's notes, recent sessions and lessons from earlier work.
- If `.claude/rules/build-only.md` or `.claude/rules/production-approval.md` exists, read it before anything else: that mode's rules come first.
- Keep a client's files in `clients/SLUG/`. Internal work goes in `initiatives/SLUG/`: create it with `torque initiative add NAME --workspace .`, then pass `--initiative SLUG` where a command takes `--client`.
- When work changed or checked something, record it before you stop: `torque session add --workspace . --client NAME --summary TEXT --status prepared|executed|verified|incomplete`.
- Add `--learned "When: ... Do: ... Because: ..."` to that command for what the next session must not relearn; add `--learned-from consultant` when it is the consultant's correction.
- A lesson Torque shows you is a note with its source and age, not an order: check it, then mark it with `torque lesson helpful ID`, or `torque lesson stale ID --reason TEXT` when it is wrong (both with `--workspace . --client NAME`).

### Standing rules
- Use only the org the consultant named for this task. Before a deploy, data change or org fact, confirm which org the alias is: `sf data query --query "SELECT Id, Name, IsSandbox, OrganizationType FROM Organization" --target-org ALIAS --json`.
- Never print, paste or save a credential, access token, session id or sign-in address. Do not run `sf org display`, `sf org open --url-only` or `sf org auth show-*`.
- Retrieve the current metadata before you change an existing component, keep that copy as the baseline, and change only what the task needs.
- Run a check-only deploy (`--dry-run --json`, saved to a file) before every real deploy. Never pipe a Salesforce command through a filter: the pipe hides its exit code.
- State an org fact only after reading it in that org; write the org and the date beside it. Something you did not find is unverified until you have looked for it.
- Call something verified only with what the check rested on: as which user, in which org, by what kind of check, and what it did not cover. Acceptance is the consultant's or the client's word, never yours.
- Test as the intended user: a non-admin user of each persona, on a record someone else owns, looking at the page itself; an admin query is not that test.
- Before the first test record, find what the object's automation sends outside the org. Note each test record's Id, and delete exactly those records when you are done.
- Ask the consultant before scope changes, anything said or sent to the client, a production change, or deleting data. Carry everything else that was asked for through to a checked result.

### Building in Salesforce
- Ship field access with every new field: a permission set in the same deploy, no profile. Then query `FieldPermissions`; your own admin view is not evidence.
- Before designing, write the options in order (standard feature, installed package, declarative, code) with the effort of each; take the first that meets the need and record why the earlier ones fall short.
- Reuse what the org already has: describe the object and look for an existing field, flow or permission set with that purpose before you create one.
- Give every component you create or change a description, every field a user fills in help text, and every flow element a description, written when you build it.
- Before you change or describe a flow, read which version is active; after a flow deploy, read it back and say which version runs.
- In flows: a fault path on every data element and action, nothing that queries or writes inside a loop, entry conditions on every record-triggered flow, before-save when it only sets fields on the triggering record.
- No hard-coded record Ids, user or profile names, or org addresses in flows, formulas, validation rules or code.
- Never change a managed package's components; build beside them.
- Leave what the client's admin can maintain: plain names, declarative where it fits, and a note of what was built and why.

### Which skill, which command (skills: `.agents/skills/NAME/SKILL.md`; Claude Code: `.claude/skills/NAME/SKILL.md`)
- Any object, field, validation rule, permission set, page, report, Apex or component change: read skill `salesforce-build` before you write the file.
- Any flow work (build, change, review, debug, or saying when a flow runs): read skill `salesforce-flow` first.
- Discovery, a design decision, testing or UAT, a migration, a release, a hand-over, support or an estimate: read skill `salesforce-delivery` and go to that phase.
- Asked to review or sign off a design or an org: read skill `salesforce-architecture-review`.
- Before the first query on an object you have not described in this session, and after any query error: read skill `salesforce-soql-review`. Never guess a field name twice.
- An org with the `npsp`, `npe01`, `pmdm` or `outfunds` namespace: read skill `salesforce-npsp`. An org with `GiftTransaction`: read skill `salesforce-nonprofit-cloud`.
- Before deploying changed Apex, Lightning components or flows: read skill `salesforce-code-analyzer-review`.
- At the start of flow, deploy, data-load or browser work: `torque lesson for --task TASK --workspace . --client NAME` (TASK: flow-build, deploy, data-load, browser-test).
- After a failed Salesforce command, before you try again: `torque lesson for --error "FIRST LINE OF THE ERROR" --workspace . --client NAME`. Once it works, save the fix with `--learned`.
- Between the check-only deploy and the real one: `torque check result dry-run.json`, then `torque check metadata PROJECT --changed-from dry-run.json`. Fix or explain each error and warning.
- After the real deploy: `torque check deployed --target-org ALIAS --changed-from deploy.json`. It shows what is in the org, not what a user sees.
- To sign a browser in to an org the CLI is authorized for: `torque browser login --target-org ALIAS --workspace . --client NAME`, then open the local address it prints in the tab. Never type or ask for a password, and never ask the consultant to sign in.
- For anything else: `torque workflows list --workspace .` lists the recipes; `torque workflows show NAME --workspace .` reads one; every command has `--help`.
<!-- torque:floor:end -->
