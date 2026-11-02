# Build-only mode

This workspace is in build-only mode. The gate hook blocks most of what the mode
does not allow. Follow these rules whether or not a call is blocked:

- No org access. Do not run `sf`/`sfdx` against any org, or use a Salesforce MCP
  tool. The CLI's local commands still work: generators (`sf apex generate`,
  `sf template generate`, `sf schema generate`, `sf cmdt generate` except `fromorg`),
  `sf project convert`, `sf code-analyzer run`/`rules` scoped out of `clients/`, the
  informational ones (`sf help`, `sf which`, any command's `--help`, `sf --version`),
  and `which sf` to check the CLI is installed.
- Client context is off limits. Do not read, list, search or write anything under
  `clients/`, and do not run `torque` other than `demo`, `workflows` and `doctor`
  without `--client`. You can still draft metadata, code, tests and a plan outside it.
- Leave `workspace.json` and the hook configuration (`.claude/settings*.json`,
  `.agents/hooks.json`) alone. Only the workspace owner changes them.
- A scope block says how to narrow the call (a path under `project/`, a git scope, a
  patch location): you may retry once with the scope it names, out of `clients/` and
  reaching no org. A boundary block (org access, client context, `workspace.json`,
  the hook configuration, the Torque package) is not to be worked around through
  another tool, script, copy, link or spelling: stop and tell the consultant what was
  blocked and why. They run the command themselves, or change the mode with
  `torque workspace ai-access`.
