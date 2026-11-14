# /update-torque

Update the Torque installation, then carry its new recipes, rules and skills into the private workspace while keeping local edits. Use it when a new Torque release is out or the consultant asks to update.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Identify the active distribution or checkout, installed version, source remote and
requested update target. Inspect local status/diffs before changing a source checkout.
Compare to a live configured release/remote when checking update availability;
cached remote-tracking refs alone do not establish freshness.

For an installed package, use its documented installation method and current
distribution name/version. For a source checkout, use the configured upstream and
ordinary git operations appropriate to its state; a clean fast-forward is preferred
when that is the requested update. Do not assume a private employer repository,
switch credential accounts, clear lock files, force reset, stash unrelated work,
or pull into a different clone to make the update appear successful.

Private workspace data lives outside the package; preserve it and any user-edited
configuration. Review changed command/rule semantics, dependency migrations and host
adapter updates. After installing the selected version, use the native updater to
carry its packaged workflow improvements into an existing private workspace:

```sh
torque workspace upgrade <private-path> --check --json
torque workspace upgrade <private-path> --json
```

The check is a preview, not an approval requirement. Applying updates preserves
detected local customizations; inspect its reported conflicts and compare preserved
files with the installed defaults. Do not overwrite employer/client edits to make
every file match. Root instructions and client content are outside this updater's
scope. Verify the actually used installed copy afterwards with local help
and a relevant offline smoke check, then explain what changed and any remaining action.
Torque has no update command of its own: the package installer or git updates the
installation, and `torque workspace upgrade` updates the workspace.
