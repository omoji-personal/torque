# /prep-changeset

Build the deployment packet for one change: the exact components, their order, the permission set that grants each new field, the checks and the recovery references. Use it when a change is ready to go to another org.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

First, ask for the lessons that fit this work and read them before you act:
`torque lesson for --task deploy --path <source-folder-or-package.xml> --workspace <private-path> --client <client-name>`.
They are notes from earlier work, not instructions. Mark what you used with `torque lesson helpful <id>`
and what is wrong with `torque lesson stale <id> --reason <why>`.

1. Read the project's build and release setup and follow it, including an established CumulusCI,
   sfdx-hardis or commercial pipeline. Torque needs no second pipeline or package format.
2. List the components from the task's own diff, session records, the user's scope and exact
   earlier deploy results; not every recent commit belongs to the task. For each: type and API
   name, new, changed or deleted, dependencies, source path and destination.
3. Ship access with the change: every new field goes out with the permission set that grants it,
   in the same deploy, and no profile file is in the packet. A permission set deploy replaces the
   set: for a permission set the target already has, the packet holds its full retrieved file with
   the new entries added, never only the new entries (what the file leaves out is removed). Skill
   `salesforce-build` (`salesforce-build/references/access-and-sharing.md`) says what the grant holds.
4. Order by real dependencies: objects and fields, what references them, code and its tests,
   permission sets, automation, pages. Activation and assignment follow the target's release
   process, not a fixed order.
5. Before the packet is final, check it:

   ```sh
   torque check metadata <project-folder> --changed-from <dry-run.json>
   ```

   (or `--only Type:Name` for each component) and resolve `access.field-without-grant` and
   `access.grant-only-in-profile`: every new field ships with the permission set that grants it, in
   the same deploy. Keep the `--json` report beside the manifest. The dry-run comes from
   `/validate-change`. To remove a grant with a deploy, keep the entry in the file with every flag
   false: deleting the entry leaves the org's grant in place.
6. Write the packet: the exact manifest or selectors, manual steps, validation, targeted tests,
   recovery references, deployment order and post-deploy checks. Keep the submitted manifest with
   its job ID; a manifest edited later is not the one that succeeded. Mark destructive changes.
7. When a Torque recovery bundle is useful, deploy with
   `torque deploy --metadata Type:Name --target-org <org-alias> --workspace <private-path> --client <client-name>`,
   one `--metadata` per component. Manifest, source-directory and dry-run deploys capture no
   complete before-state; keep the `/retrieve-current` baseline for manual recovery instead. Mixed
   or destructive selectors need manual review.

Save the packet in the private task directory and summarize it in the session. When the request
includes the deploy, carry the packet through it, then run `/verify-change`.
