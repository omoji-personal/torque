# /status

Check the selected workspace, installed tools, and requested org connections.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Read `torque context --workspace <path> --client <name> --json` when a client is selected.
Inspect Torque and Salesforce CLI versions and the selected host's configured integrations.
Do not dump credential-bearing config or raw `sf org display --json` into chat.

For each org in the requested scope, resolve auth and summarize only org ID, alias,
username or role where appropriate, instance, and connection status. Use live
Organization.IsSandbox when classification matters; URL shape alone is not authoritative.
Check a configured JWT certificate's expiry without reading its private key.

Show: installed / unavailable / untested integrations, current client, connected or
expired orgs, recent session, pending work, and any context mismatch. Broader multi-client
connection checks run only when that broader inventory is the task. A missing optional
browser or model integration does not make the whole workspace unusable.

For “latest version,” identify the installed distribution or current checkout and
compare to its configured release/remote when network checking is in scope. State
“not checked” if only local state is known. Do not clear git locks, change auth accounts,
update packages, or launch org write probes as a status side effect.
