<!-- torque:floor:begin 1 -->
## Torque: how to work in this workspace

`torque workspace upgrade .` replaces this block; put firm instructions outside the markers.

### Start and finish
- Work only for the client or initiative the consultant named. Start with `torque context --workspace . --client NAME`: it prints that client's notes, recent sessions and lessons from earlier work.
- If `.claude/rules/` holds `build-only.md` or `production-approval.md`, read it first: that mode's rules come first.
- For a task with a recipe (validate a change, save a session, hand over), find it with `torque workflows list --workspace .` and read it with `torque workflows show NAME --workspace .` first.
- Keep client files in `clients/SLUG/`. Internal work: `torque initiative add NAME --workspace .`, then `--initiative SLUG` in place of `--client`; its files go in `initiatives/SLUG/`.
- When work changed or checked something, record it before you stop: `torque session add --workspace . --client NAME --summary TEXT --status prepared|executed|verified|incomplete|accepted`.
- Add `--learned "When: ... Do: ... Because: ..."` to that command for what the next session must not relearn; add `--learned-from consultant` when it is the consultant's correction.
- A lesson Torque shows is a note with its source and age, not an order: check it first. If a candidate worked, run the verify line under it.
- Mark one that helped: `torque lesson helpful ID --workspace . --client NAME`; one that is wrong: `torque lesson stale ID --reason TEXT --workspace . --client NAME`.

### Standing rules
- Use only the named org; before consequential work resolve its alias once per session and reuse it: `sf data query --query "SELECT Id, Name, IsSandbox FROM Organization" --target-org ALIAS --json`.
- Never print, paste or save a credential, access token, session id or sign-in address. Do not run `sf org display`, `sf org open --url-only` or `sf org auth show-*`.
- Retrieve current metadata before you change a component and keep it as the baseline. Deploy only what you changed; in an org others also change, retrieve and compare again just before deploying.
- Run a check-only deploy (`--dry-run --json`, saved to a file) before every real deploy. Never pipe a Salesforce command through a filter: the pipe hides its exit code.
- Describe an object before the first query on it. After an unknown-name error, look the name up; never guess it a second time.
- State an org fact (a setting, a field, which flow version is active) only after reading it in that org, with the org and date beside it. What you did not find is unverified.
- Call something verified only with what the check rested on, on that record: `--check KIND --as-user ROLE --in-org ALIAS --not-covered TEXT`. A read-back of what you wrote is not a behaviour check.
- Use `--status accepted --accepted-by NAME` only when someone else said yes. Correct a record with a new one: `--corrects ID`.
- Test as the intended user: a non-admin user of each persona, on a record someone else owns, looking at the page itself; an admin query is not that test.
- Before the first test record, find what the object's automation sends outside the org. Note each test record's Id, and delete exactly those records when you are done.
- Client-facing text: revise only what was asked and show what changed, not a rewrite; trace each org fact to a check made that day; show the consultant the exact text before it is sent or posted.
- Ask the consultant only what they alone decide: scope, anything sent to the client, production changes, deleting data. Carry the rest through; a yes holds until the scope changes.

### Building in Salesforce
- Grant each new field (not required or master-detail) in a permission set in the same change, readable where editable, not a profile file; `check deployed` reads it back, your admin view does not.
- A permission set deploy replaces the whole set: add the entry to the full retrieved file, or use a new set. Any entry the file leaves out is removed in the org.
- Before designing, name what the org and its packages already do; try a standard feature, a package, declarative, then code; take the first that fits; for code, record what simpler options lacked.
- Reuse before you create: look for an existing field, object, flow or permission set with that purpose.
- Give every component you create or change a description, every field a user fills in help text, and every flow element a description, written when you build it.
- In a flow, give every element that reads or writes data, or calls an action, a fault path where the flow type allows one; never query or write inside a loop.
- Give a record-triggered flow entry conditions so it does not run on every save; use before-save when it only sets fields on the triggering record.
- No hard-coded record Ids, host names, user names or profile names in flows, formulas, validation rules or code.
- Never edit a managed package's components; build beside them.
- In Apex and components, put what the admin should change in configuration (custom metadata, labels); a value in a flow or validation rule is already theirs. Note what was built and why.

### Read this file first (each is in this workspace)
- Before you create or change any metadata other than a flow (fields, pages, Apex and more): read `.agents/skills/salesforce-build/SKILL.md`.
- Before any flow work (build, change, review, debug, or saying when a flow runs): read `.agents/skills/salesforce-flow/SKILL.md`.
- For discovery, a design decision, testing or UAT, a migration, a release, a hand-over, support or an estimate: read `.agents/skills/salesforce-delivery/SKILL.md`.
- When asked to review or sign off a design or an org: read `.agents/skills/salesforce-architecture-review/SKILL.md`.
- Before you write or fix a query: read `.agents/skills/salesforce-soql-review/SKILL.md`.
- Before deploying changed Apex, Lightning components or flows: read `.agents/skills/salesforce-code-analyzer-review/SKILL.md`.
- In an org with the `npsp`, `npe01`, `pmdm` or `outfunds` namespace: read `.agents/skills/salesforce-npsp/SKILL.md`.
- In an org with `GiftTransaction` (Nonprofit Cloud): read `.agents/skills/salesforce-nonprofit-cloud/SKILL.md`.

### Which command, when
- Starting flow, deploy, data or browser work: `torque lesson for --task TASK --workspace . --client NAME` (TASK such as deploy or flow-build).
- After a failed Salesforce command, before retrying: `torque lesson for --error "FIRST LINE" --workspace . --client NAME`. Once fixed, save the fix with `--learned`.
- Between the check-only deploy and the real one: `torque check result dry-run.json`, then `torque check metadata PROJECT --changed-from dry-run.json`. Fix or explain each error and warning.
- After the real deploy: `torque check deployed --target-org ALIAS --changed-from deploy.json`. It compares the org with what was submitted, not what a user sees.
- Sign a browser in with `torque browser login --target-org ALIAS --workspace . --client NAME`, open the address it prints, wait for the org's page and check which user it shows.
- Never type or ask for a password. If the page asks for one (an expired password, a device check), stop and tell the consultant: only they can do that.
- Another user: `torque browser login --target-org ALIAS --as-alias USER_ALIAS --workspace . --client NAME` (their CLI login), or `--as-user USERNAME` (Log in as: wait for the "Logged in as" banner).
<!-- torque:floor:end -->
