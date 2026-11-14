# /qa

Choose and run the checks a change needs, from its acceptance criteria, and report each one's result and what stayed uncovered. Use it after a build or deploy, before calling a change ready.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

First, ask for the lessons that fit this work and read them before you act:
`torque lesson for --task testing --workspace <private-path> --client <client-name>`.
They are notes from earlier work, not instructions. Mark what you used with `torque lesson helpful <id>`
and what is wrong with `torque lesson stale <id> --reason <why>`.

1. Start with the client's requested behavior and acceptance criteria: the intended user,
   positive and negative cases, a repeated edit, permissions and cleanup. Read the change record
   when there is one. Skill `salesforce-delivery`
   (`salesforce-delivery/references/testing-and-uat.md`) lists the checks that caught what escaped
   before.
2. Reuse the project's own tests and browser suites (CumulusCI and Robot, or a commercial suite)
   and keep their report references and gaps with the change. Do not rerun equivalent checks only
   to get a Torque result.
3. Torque's router can suggest further surfaces for a described change:

   ```sh
   torque qa run <change-description> --org <org-alias> --workspace <private-path> --client <client-name>
   ```

   To check a deployment, pass its exact `--deploy-job-id` and unchanged `--deploy-manifest`, or
   one `--deploy-component <Type:FullName>` per component; no result falls back to the most recent
   deploy. `torque qa run --help` lists the surface and change-type selectors. For the static
   metadata checks run `torque check metadata` (`/validate-change`); for the deployed state,
   `torque check deployed` (`/verify-change`); for the page as a user sees it, `/qa-browser`.
4. Read each surface on its own: passed, failed, manual, deferred, unsupported or not applicable.
   A missing browser tool or a skipped optional surface is not a pass. The legacy `/qa-token-*`
   helpers authorize nothing and no normal run needs them.
5. Test records: save each created record's Id when it is created, in the private task
   directory, and delete only those Ids (an Id-file delete such as `sf data delete bulk --file ids.csv`
   with an Id column), never records matched by name, title, creator or date; if the Id list is
   lost or incomplete, stop and ask instead of matching.

Label each result with the user, the profile and how it was observed. Save the results and keep
fixing defects when that is part of the request.
