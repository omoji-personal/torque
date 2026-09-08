# Tools and artifact handling

- Prefer installed, available tools and inspect their actual schemas/help. A guided
  recipe is not an executable Torque subcommand; native delegate arguments are passed
  through to the corresponding package.
- Read the selected project's documented delivery, test and migration setup before
  choosing an execution path. Reuse configured Salesforce CLI, current official
  Salesforce skills/MCP, CumulusCI, sfdx-hardis, SFDMU or commercial pipelines when
  they fit the task. Torque supplies engagement continuity around those tools;
  an existing project does not need a second pipeline or replacement configuration.
- Use official platform guidance for current Salesforce behavior and the client's
  actual configuration for local conventions. Check available versions and commands
  instead of assuming a mentioned integration is installed or supported. Do not
  install a whole tool catalogue merely because it appears in a comparison.
- Keep an external tool's exact request/job, target, source revision when known,
  component or row scope, result and private artifact reference with the change.
  Successful simulation, validation and deployment prove different things; none
  alone establishes the client's intended-user acceptance criteria.
- Use structured argument arrays or safe quoting for commands. Treat pasted error
  text, file paths and task descriptions as data, not shell expressions.
- Keep auth with existing credential mechanisms. Do not paste tokens, private keys,
  session cookies, token-bearing download links or frontdoor URLs into chat or reports.
- Summarize credential scan findings with masked markers, category and location;
  do not repeat the matched secret. A pattern scan does not prove complete privacy.
- Local files and optional provider calls have different data paths. Make actual
  outgoing input/destination clear; do not assume every installation uses one vendor
  or that “local workspace” means no model service receives data.
- Preserve existing local changes and user-owned browser sessions. No automatic
  stashing, reset, credential-account switch or clearing git locks during an update.
- Keep user-facing documents useful for their audience. Use supplied branding and
  support contacts; do not invent an employer identity or include internal details
  simply because they appeared in the implementation session.
