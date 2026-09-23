# /new-client

Create a private client workspace and establish a useful engagement baseline.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Infer the client name, workspace and engagement type from the request. If no workspace
exists, initialize a user-chosen private directory outside the public source checkout:

```sh
torque workspace init <private-path> --name <workspace-name> --profile generic
torque client add <client-name> --workspace <private-path> --org <org-alias>
torque context --workspace <private-path> --client <client-name> --json
```

Omit `--org` when connection is not available yet. `solution-lead` is an optional
workspace profile, not a client identity or proof of employer tool approval.
Record engagement purpose, stakeholders supplied by the user, systems, current task,
artifact locations and org connections. The CLI's client configuration is authoritative
for its paths; do not manually invent a second client index.

Read the client's existing project instructions and delivery setup. Preserve its
repositories, CI/release pipeline, test runner, migration mappings and preferred
Salesforce tools. If this will help future sessions, record the actual project
paths, installed versions, documented commands and known limits in the client's
`context/tooling.md`; `torque context` reads this file with the other client notes.
Keep unknowns explicit. Reuse a working CumulusCI, sfdx-hardis, SFDMU or commercial
setup instead of creating a parallel stack. This note is optional and does not
need to become an onboarding questionnaire or an installation prerequisite.

If connection was requested, use `/connect-org`. Run a scoped `/audit-org` and `/snapshot`
when they help the engagement; do not force a full audit merely to create a folder.
Initialize the session with `/session-save`, including anything not yet connected or
verified. Offer relevant next work through `/discovery`, implementation, or support.
