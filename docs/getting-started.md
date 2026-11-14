# Getting started: one ticket, start to finish

This page takes one made-up ticket through Torque in the order you meet the commands. The
client (`sample`), the org alias (`sample-sandbox`) and every name are fictional; use a
sandbox your Salesforce CLI is already authorized for. Install Torque first
([installation](installation.md)). The [consulting walkthrough](../examples/consulting-walkthrough.md)
tells the same kind of story in conversation; the [offline demo](demo.md) needs no org at all.

**The ticket:** "Membership staff need a Tier picklist on Account (Gold, Silver, Bronze),
with help text, that they can see and edit."

## 1. Set up the workspace (once)

```sh
torque workspace init ../torque-private --name "My consulting workspace"
torque doctor --workspace ../torque-private --for salesforce
torque client add sample --workspace ../torque-private --org sample-sandbox
```

`init` writes a private folder with its own `AGENTS.md`. The Torque block in it is the text
Claude Code, Codex and Antigravity all load at the start of every session: the standing
rules, the Salesforce build practice and which skill or command to use when
([hosts](hosts.md)). `doctor` reports what is installed and whether each host loads that
block. `client add` records the alias only; it does not log in.

Open `../torque-private` in your agent and give it the ticket. Everything below is what the
block tells the agent to do; you can run each command yourself as well.

## 2. Resume and look for earlier lessons

```sh
torque context --workspace . --client sample
torque lesson for --task field-build --type CustomField --workspace . --client sample
```

`context` prints the client's notes, recent sessions and the lessons from earlier work.
`lesson for` asks for the ones that fit this task. A lesson is a note with its source and
age, not an instruction.

## 3. Build with the skills

Before writing a file the agent reads `.agents/skills/salesforce-build/SKILL.md` (and
`.agents/skills/salesforce-flow/SKILL.md` for any flow); every workspace has all eight
practice skills there, and Claude Code also lists them as skills. For this ticket that means: check that Account has no field with this purpose
already; a restricted picklist with a description and help text; and a permission set
`Membership_Staff` with read and edit access to the field, in the same change, no profile.
It retrieves the current metadata first and keeps that copy as the baseline.

## 4. Validate, then check

```sh
sf project deploy start --source-dir force-app --target-org sample-sandbox --dry-run --json > dry-run.json
torque check result dry-run.json
torque check metadata force-app --changed-from dry-run.json
```

The check-only deploy goes to a file, never through a pipe, which would hide its exit code.
`check result` puts the validation on one screen. `check metadata` checks only what the
validation reports as created or changed: here it would report a field without a grant, a
missing description or help text, and a profile in the change. Fix each error and warning,
or record why one does not apply, then validate again ([checks](checks.md)).

## 5. Deploy, then check what landed

```sh
sf project deploy start --source-dir force-app --target-org sample-sandbox --json > deploy.json
torque check deployed --target-org sample-sandbox --changed-from deploy.json --as-permission-set Membership_Staff
```

`check deployed` reads the org: whether each component matches the source, and which
permission sets grant the new field. It does not show what a user sees. For a deploy that
keeps a recovery snapshot, deploy through Torque instead, one component per `--metadata`:

```sh
torque deploy --workspace . --client sample --target-org sample-sandbox --metadata CustomField:Account.Tier__c
```

## 6. Test as the membership user

```sh
torque browser login --target-org sample-sandbox --workspace . --client sample
```

It prints a local address; the agent opens it in its browser tab, which lands signed in. No
password is typed and the sign-in address is never shown. To test as a membership user rather
than as yourself, authorize that user's alias in the CLI once and add `--as-alias`
([browser sign-in](browser-sign-in.md)). Look at the page as that user, on a record someone
else owns.

## 7. Record the work and what it taught

```sh
torque session add --workspace . --client sample --summary "Tier picklist and Membership_Staff deployed to sample-sandbox; checked as a membership user" --status executed --learned "When: a new field on Account. Do: ship the permission set in the same deploy. Because: the field is invisible without it."
```

`--learned` saves a lesson with the record. When the consultant corrected the agent, add
`--learned-from consultant`; that lesson is active at once.

## 8. When a command fails

```sh
torque lesson for --error "No such column 'Tier__c' on entity 'Account'" --workspace . --client sample
```

Before trying again, the agent asks whether an earlier session already met this error. Once
the fix works, it saves it with `--learned` on the next session record.

## 9. Hand over

```sh
torque handoff --workspace . --client sample
```

The next consultant, or the next session, starts from the context command of step 2 and this
hand-off.
For every command: `torque --help` and `torque COMMAND --help`. For the recipes:
`torque workflows list --workspace .`.
