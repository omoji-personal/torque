# /validate-change

Validate a change before it is deployed: a check-only deploy read from a file, Torque's metadata check on what that deploy would create or change, and the tests the change needs. Use it before every real deploy.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

First, ask for the lessons that fit this work and read them before you act:
`torque lesson for --task deploy --path <source-folder-or-package.xml> --workspace <private-path> --client <client-name>`.
They are notes from earlier work, not instructions. Mark what you used with `torque lesson helpful <id>`
and what is wrong with `torque lesson stale <id> --reason <why>`.

1. Name the target org, the exact manifest or component list, the DX project and the expected
   outcome. When the project has its own validation path (CI, CumulusCI, sfdx-hardis, a commercial
   pipeline), use it and keep its job, target, source and report with the change.
2. Validate with the Salesforce CLI and write the result to a file with `--json`. Never run it
   through a pipe or filter: the pipe's exit code hides the CLI's.

   ```sh
   sf project deploy start --manifest <package.xml> --target-org <org-alias> --dry-run --json > dry-run.json
   torque check result dry-run.json
   torque check metadata <project-folder> --changed-from dry-run.json
   ```

   `torque check result` says whether the validation passed, with each component failure and line
   and each test failure. `torque check metadata` checks only what the dry-run reports as Created or
   Changed: field access for new fields, descriptions and help text, fault paths, data elements in
   loops, entry conditions, hard-coded Ids, API versions, profiles and managed components, with
   Salesforce Code Analyzer's flow rules merged in when it is installed. Fix each error and warning
   in the files, or record why one does not apply (an entry with a reason in `torque-check.yml`),
   then validate again. Say in the summary what its last line lists as not checked, and do not
   claim Code Analyzer coverage when the report says it did not run.
3. For Apex, Lightning components and flows, read skill `salesforce-code-analyzer-review`. For
   Apex, validate with `--test-level RunSpecifiedTests` and one `--tests` per test class the change
   needs.
4. Test what the change is for, as the intended user (`/qa`, `/qa-browser`). Test records,
   including records automation creates: save each created record's Id when it is created, in the
   private task directory, and delete only those Ids (an Id-file delete such as
   `sf data delete bulk --file ids.csv` with an Id column), never records matched by name, title,
   creator or date; if the Id list is lost or incomplete, stop and ask instead of matching.
5. Deploy. A validation creates no Torque recovery bundle. When the write needs one, deploy with
   `torque deploy --metadata Type:Name --target-org <org-alias> --workspace <private-path> --client <client-name>`,
   one `--metadata` per component. Manifest, source-directory and dry-run deploys capture no
   complete before-state: keep the `/retrieve-current` baseline for those, and before a quick
   deploy of a validated job.

Record the validation job, the selectors, the result file and what passed, failed, was not
applicable or was not checked. Follow the deploy with `/verify-change`.
