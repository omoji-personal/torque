# Build-only mode

This workspace is in build-only mode. The gate hook blocks most of what the mode
does not allow. Follow these rules whether or not a call is blocked:

- No org access. Do not run `sf` or `sfdx` against any org, by alias, username or
  default org, and do not use a Salesforce MCP tool. Local generators such as
  `sf apex generate`, and `sf help` and `sf --version`, still work.
- Client context is off limits. Do not read, list, search or write anything under
  `clients/`, and do not run `torque` commands other than `demo`, `workflows` and
  `doctor` without `--client`.
- Leave `workspace.json` and the hook configuration (`.claude/settings*.json`,
  `.agents/hooks.json`) alone. Only the workspace owner changes them.
- A blocked call is not to be worked around. Do not try it again through another
  tool, a script, a copy, a link or a different spelling. Stop, and tell the
  consultant what was blocked and what you needed it for.
- You can still draft metadata, code, tests, queries and a run plan in the
  workspace, outside `clients/`.
- The consultant can run the command themselves, in their own terminal outside
  this session, or change the mode with `torque workspace ai-access`.

In this workspace this rule takes precedence over "Carry the requested work through delivery".
