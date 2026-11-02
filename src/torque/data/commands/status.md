---
description: "Check the selected workspace, installed tools, and requested org connections."
---

# /status

Check the selected workspace, installed tools, and requested org connections.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Read `torque context --workspace <path> --client <name> --json` when a client is selected.
Inspect Torque and Salesforce CLI versions and the selected host's configured integrations.
Check each org with `sf data query --query "SELECT Id, Name, IsSandbox, OrganizationType FROM Organization" --target-org <org-alias> --json`;
it prints no credential. Do not run `sf org display` (Salesforce CLI releases before
2.136.8 print the access token), `sf org open --url-only`, `sf org auth show-*` or
`sf org list` (every org on the machine, other clients' included): a command's output
reaches the model whether or not it is repeated in chat, so filtering it afterwards is too late.

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

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
