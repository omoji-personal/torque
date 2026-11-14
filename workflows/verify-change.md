# /verify-change

Verify that a deployed or configured change is in the org as intended and works for the people it is for, and record what is still unchecked. Use it after every deploy or configuration change.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

First, ask for the lessons that fit this work and read them before you act:
`torque lesson for --task deploy --path <source-folder-or-package.xml> --workspace <private-path> --client <client-name>`.
They are notes from earlier work, not instructions. Mark what you used with `torque lesson helpful <id>`
and what is wrong with `torque lesson stale <id> --reason <why>`.

1. Name the org, the change, its deploy job (query that job, never "the latest deployment"), the
   business criteria and the intended users. Existing test or pipeline evidence counts when its
   target, source, scope and date fit this change.
2. Read the deploy back:

   ```sh
   torque check deployed --target-org <org-alias> --changed-from <deploy.json> --as-permission-set <permission-set>
   ```

   Read-only, it reports whether each component in the org matches the submitted source, whether
   each flow's latest version is the active one, which permission sets grant each new field, and
   whether each validation rule is active. It does not show assignment, what a user sees or whether
   the change does what was asked; the checks below and a test as the intended user do.
3. Then what the read-back cannot see:
   - Fields: the permission set is assigned to the intended users; other permission sources
     (groups, muting), record access, and the field on the page those users open.
   - Flows: entry, negative, fault and bulk paths as the change requires;
     `torque advisory flow --target-org <org-alias> --api-name <FlowApiName>` names the active version.
   - Pages: activation and assignment, then the page as the user sees it.
   - Settings and data: read back the exact keys and rows and their side effects; counts alone do
     not prove a migration.
4. Test as the intended user, not as the admin who deployed:
   `torque browser login --target-org <org-alias> --as-alias <user-alias>` signs a tab in as that
   user (`/qa-browser`, `/qa-multiprofile`). Label each result with the user and the route. Test
   records, including records automation creates: save each created record's Id when it is
   created, in the private task directory, and delete only those Ids (an Id-file delete such as
   `sf data delete bulk --file ids.csv` with an Id column), never records matched by name, title,
   creator or date; if the Id list is lost or incomplete, stop and ask instead of matching.
5. When a change record exists, record each criterion with `torque change check` and the exact
   deploy observation with `torque change verify-deploy`; neither stands in for the other.

Fix defects found within scope and check again. Save a session entry that says what was verified,
as whom, in which org, by what kind of check, and what another user or tester still has to do.
