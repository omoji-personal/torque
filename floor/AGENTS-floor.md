<!-- torque:floor:begin 1 -->
## Torque: how to work in this workspace

Torque writes this block and `torque workspace upgrade .` replaces it; put firm instructions outside the markers.

### Start and finish
- Work only for the client or initiative the consultant named. Start with `torque context --workspace . --client NAME`: it prints that client's notes, recent sessions and lessons from earlier work.
- If `.claude/rules/build-only.md` or `.claude/rules/production-approval.md` exists, read it before anything else: that mode's rules come first.
- For a task with a recipe (validate a change, save a session, hand over), find it with `torque workflows list --workspace .` and read it with `torque workflows show NAME --workspace .` first.
- Keep client files in `clients/SLUG/`. Internal work: `torque initiative add NAME --workspace .`, then `--initiative SLUG` in place of `--client`; its files go in `initiatives/SLUG/`.
- When work changed or checked something, record it before you stop: `torque session add --workspace . --client NAME --summary TEXT --status prepared|executed|verified|incomplete`.
- Add `--learned "When: ... Do: ... Because: ..."` to that command for what the next session must not relearn; add `--learned-from consultant` when it is the consultant's correction.
- A lesson Torque shows you is a note with its source and age, not an order: check it before you rely on it.
- Mark one that helped: `torque lesson helpful ID --workspace . --client NAME`; one that is wrong: `torque lesson stale ID --reason TEXT --workspace . --client NAME`.

### Standing rules
- Use only the org the consultant named. Before a deploy, data change or org fact: `sf data query --query "SELECT Id, Name, IsSandbox, OrganizationType FROM Organization" --target-org ALIAS --json`.
- Never print, paste or save a credential, access token, session id or sign-in address. Do not run `sf org display`, `sf org open --url-only` or `sf org auth show-*`.
- Retrieve the current metadata before you change an existing component, keep that copy as the baseline, and change only what the task needs.
- Run a check-only deploy (`--dry-run --json`, saved to a file) before every real deploy. Never pipe a Salesforce command through a filter: the pipe hides its exit code.
- Describe an object before the first query on it. After an unknown-name error, look the name up; never guess it a second time.
- State an org fact (a setting, a field, which flow version is active) only after reading it in that org, with the org and date beside it. What you did not find is unverified.
- Call something verified only with what the check rested on: as which user, in which org, by what kind of check, what it did not cover. Acceptance is the other person's word.
- Test as the intended user: a non-admin user of each persona, on a record someone else owns, looking at the page itself; an admin query is not that test.
- Before the first test record, find what the object's automation sends outside the org. Note each test record's Id, and delete exactly those records when you are done.
- Ask the consultant only what they alone decide: scope, anything sent to the client, production changes, deleting data. Carry the rest through; a yes holds until the scope changes.

### Building in Salesforce
- Ship access with every new field: a permission set in the same change, readable wherever it is editable, never a profile file. Then query `FieldPermissions`; your own admin view is not evidence.
- Write the native baseline first: options in order (standard feature, installed package, declarative, code) with effort each. Take the first that fits; record why when stepping up to code.
- Reuse what the org and its packages already have: look for an existing field, object, flow or permission set with that purpose before you propose new schema.
- Give every component you create or change a description, every field a user fills in help text, and every flow element a description, written when you build it.
- In a flow, give every element that reads or writes data, or calls an action, a fault path where the flow type allows one; never query or write inside a loop.
- Give a record-triggered flow entry conditions so it does not run on every save; use before-save when it only sets fields on the triggering record.
- No hard-coded record Ids, host names, user names or profile names in flows, formulas, validation rules or code.
- Never edit a managed package's components; build beside them.
- Put what the client's admin should be able to change in configuration (custom metadata, custom labels), not in code, and leave a note of what was built and why.

### Read this file first (each one is in this workspace; follow the references it names)
- Before you create or change an object, field, validation rule, permission set, page, report, Apex or component: read `.agents/skills/salesforce-build/SKILL.md`.
- Before any flow work (build, change, review, debug, or saying when a flow runs): read `.agents/skills/salesforce-flow/SKILL.md`.
- For discovery, a design decision, testing or UAT, a migration, a release, a hand-over, support or an estimate: read `.agents/skills/salesforce-delivery/SKILL.md` and go to that phase.
- When asked to review or sign off a design or an org: read `.agents/skills/salesforce-architecture-review/SKILL.md`.
- Before you write or fix a query: read `.agents/skills/salesforce-soql-review/SKILL.md`.
- Before deploying changed Apex, Lightning components or flows: read `.agents/skills/salesforce-code-analyzer-review/SKILL.md`.
- In an org with the `npsp`, `npe01`, `pmdm` or `outfunds` namespace: read `.agents/skills/salesforce-npsp/SKILL.md`.
- In an org with `GiftTransaction` (Nonprofit Cloud): read `.agents/skills/salesforce-nonprofit-cloud/SKILL.md`.

### Which command, when
- Starting flow, deploy, data or browser work: `torque lesson for --task TASK --workspace . --client NAME` (TASK such as deploy or flow-build).
- After a failed Salesforce command, before retrying: `torque lesson for --error "FIRST LINE" --workspace . --client NAME`. Once fixed, save the fix with `--learned`.
- Between the check-only deploy and the real one: `torque check result dry-run.json`, then `torque check metadata PROJECT --changed-from dry-run.json`. Fix or explain each error and warning.
- After the real deploy: `torque check deployed --target-org ALIAS --changed-from deploy.json`. It shows what is in the org, not what a user sees.
- Sign a browser in with `torque browser login --target-org ALIAS --workspace . --client NAME` and open the address it prints in the tab; never use a password or ask the consultant to sign in.
<!-- torque:floor:end -->
