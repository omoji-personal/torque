# /connect-org

Connect Salesforce using the existing CLI or available host integration.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Use the requested alias and login domain. Infer the intended environment from supplied
context, then let actual org metadata establish identity after login. Inspect existing
authentication before starting a new login. Use the host's available Salesforce connector
or the installed Salesforce CLI, for example:

```sh
sf org login web --alias <org-alias> --instance-url https://test.salesforce.com
```

Use the actual login/My Domain URL for the org; production commonly uses
`https://login.salesforce.com`. Keep the interactive authentication step with the user.
For an existing JWT/connected application, use its configured credential files and
current Salesforce setup requirements; do not create a broadly privileged connected
application or request full scopes as the automatic fallback.

Verify the connection and query Organization identity/type. Save the alias and verified
identity in the private client context. Never print access tokens, frontdoor URLs or keys.
An authenticated org can be used for work already authorized by the user; Torque adds
no production read-only toggle or separate sandbox permission grant.

MCP is optional. Inspect the actual host/tool schemas before configuring an integration;
do not invent tools named after a client or rewrite the user's global host config.
