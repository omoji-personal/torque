---
description: "Make one small, everyday change end to end: a new field, a changed field or validation rule, a report, or \"a user cannot see X\", from the client's context to a deploy checked in the org and tested as the user. Use it for a change of a few components; larger work goes through `/requirements-to-build` and `/prep-changeset`."
---

# /small-change

Make one small, everyday change end to end: a new field, a changed field or validation rule, a report, or "a user cannot see X", from the client's context to a deploy checked in the org and tested as the user. Use it for a change of a few components; larger work goes through `/requirements-to-build` and `/prep-changeset`.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

For "a user cannot see X", find the cause first (`/security-review`): usually a missing grant in a
permission set, an unassigned permission set, a page that does not show the field, or record
access. The fix is then a small change like any other.

1. Load the client: `torque context --workspace <private-path> --client <client-name>`.
2. Ask for the lessons that fit the task and read them:
   `torque lesson for --task field-build --type CustomField --workspace <private-path> --client <client-name>`
   (use the task and type of your change: `--type ValidationRule`, `--type Report`, `--task access`).
3. Read what is there: `sf sobject describe --sobject <Object> --target-org <org-alias> --json > describe.json`
   for the object and its fields, and retrieve the components you will change (`/retrieve-current`).
4. Build to the standard in skill `salesforce-build`: reuse what exists, the simplest option, a
   description on every component and help text on every field a user fills in, the field's grant
   in a permission set in the same change (never a profile), on the page its users open.
5. Check the files: `torque check metadata <project-folder> --only <Type:Name>` (one `--only` per
   component). Fix each error and warning, or record why one does not apply.
6. Validate from the project folder, writing the result to a file:

   ```sh
   sf project deploy start --metadata <Type:Name> --target-org <org-alias> --dry-run --json > dry-run.json
   torque check result dry-run.json
   torque check metadata <project-folder> --changed-from dry-run.json
   ```

   On a failed validation, fix what `torque check result` names and validate again.
7. Deploy the same components: `sf project deploy start --metadata <Type:Name> --target-org <org-alias> --json > deploy.json`
   (or `torque deploy --metadata <Type:Name> --target-org <org-alias> --workspace <private-path> --client <client-name>`
   for a recovery bundle), then `torque check result deploy.json`.
8. Read the deploy back:
   `torque check deployed --target-org <org-alias> --changed-from deploy.json --as-permission-set <permission-set>`.
   `same` is containment, not proof that it works; `/verify-change` says what each status needs.
9. Test as the intended user: `torque browser login --target-org <org-alias> --as-alias <user-alias>`
   (a separate alias: check the user's identity on the page) or `--as-user <username>` (Log in as:
   wait for the "Logged in as" banner). Open the page, do the task, and save any test record's Id.
10. Record the session with what the check rested on, and what is left in the org:

   ```sh
   torque session add --workspace <private-path> --client <client-name> --status verified --summary <summary-text> --check read-back --check rendered --as-user <role> --in-org <org-alias> --not-covered <what>
   ```

   Put "Left in the org: ..." (test record Ids, temporary settings) in the summary. `/session-save`
   has the rest.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
