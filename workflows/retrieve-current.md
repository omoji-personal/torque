# /retrieve-current

Retrieve the current version of the components a change touches, keep it as the baseline, and stage an editable copy. Use it before changing anything that already exists in an org.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

{{lessons --task retrieve}}

1. Resolve the target org and the exact metadata names from the request and the org's schema.
2. Retrieve into a task folder of the private workspace. Outside a Salesforce DX project use the
   metadata format, which needs no project:

   ```sh
   sf project retrieve start --metadata <Type:ApiName> --target-org <org-alias> --target-metadata-dir <folder> --unzip --json > retrieve.json
   ```

   Inside a DX project whose package directory exists, `--output-dir <folder-inside-the-project>`
   writes source format instead. `--output-dir` outside a project fails (the output must be inside
   the project, or the package directory named in `sfdx-project.json` does not exist). Repeat
   `--metadata`, or use `--manifest <package.xml>`, for several components.
3. Read the result file: every component you asked for came back, and none is reported as not
   found. Record org identity, time, selectors, API version and file paths. A cached copy is
   usable for offline planning when dated; it is not the org's current state.
4. Copy the baseline to a proposal folder and change only the copy. Compare the diff before
   applying it, and reconcile anything changed in the org since the retrieve. For a new component,
   record that it is absent and retrieve only what it depends on.

This baseline is for manual recovery, not a Torque recovery bundle; `/prep-changeset` says when
to deploy through `torque deploy --metadata ...` for one. Continue with `/validate-change`.
