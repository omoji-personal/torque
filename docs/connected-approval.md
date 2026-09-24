# Connected mode with per-write approval

## Host facts verified

Checked on 2026-09-24 against Claude Code 2.1.281 (the hooks and permissions
documentation current on that date, plus a live session) and Salesforce CLI
2.150.6 (`sf commands --json`, 273 commands). Connected mode depends on each fact
below; recheck them when either tool changes its hook or command contract.

| Fact | Verified value | Used by |
|---|---|---|
| PreToolUse input keys | `session_id`, `transcript_path`, `cwd`, `permission_mode`, `hook_event_name`, `tool_name`, `tool_input`, `tool_use_id` | gate: consume record, bypass check |
| `permission_mode` values | `default`, `plan`, `acceptEdits`, `auto`, `dontAsk`, `bypassPermissions` | gate |
| Force a prompt | print `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": "..."}}` on stdout, exit 0 | gate `ask` |
| Block | exit 2; stderr is the reason. Exit 2 wins over any JSON and over allow rules | gate `deny` |
| Hook `ask` in `bypassPermissions` or `auto` mode | Not documented. Bypass mode skips permission prompts, so an `ask` cannot be relied on there | gate denies unverifiable routes in both modes |
| Hook decisions and rules | A matching deny or ask rule still applies when the hook returns `allow` or `ask` | permission generator |
| Rule order | deny, then ask, then allow; the first match decides | permission generator, doctor drift |
| Bash prefix rule | `Bash(sf apex run:*)` and `Bash(sf apex run *)` are equivalent; `:*` only at the end. Ask and deny rules also match inside compound commands and substitutions | permission generator |
| MCP rule | `mcp__server` (bare) or `mcp__server__*` matches every tool of a server; an `mcp__` rule with parentheses is skipped | permission generator |
| File rules | Only `Edit(path)` and `Read(path)` are consulted; a `Write(path)` rule is accepted but never used. `path` is relative to the current directory, `/path` to the settings file's project, `//path` absolute, `~/path` from home | permission generator (uses `Edit`, not `Write`) |
| Disable bypass | `permissions.disableBypassPermissionsMode: "disable"` in any settings file | permission generator, doctor |
| `defaultMode: bypassPermissions` | Takes effect only from user or managed settings (since 2.1.257), not project settings | doctor also checks the user settings file |
| Agent environment | Tool subprocesses have `CLAUDECODE=1` and `CLAUDE_CODE_ENTRYPOINT` set | presence check |
| Agent ancestry | The Bash tool's parent process is named `claude` | presence check |
| Hook environment | The hook inherits the Claude Code process environment; `CLAUDE_PROJECT_DIR` is set | client binding (`TORQUE_CLIENT`) |

`sf` commands that only read (from the 2.150.6 summaries) and that connected mode
allows for an approved org: `data query`, `data get record`, `data search`,
`data export tree|bulk|resume`, `data bulk results`, `data resume`,
`sobject describe|list`, `org display [user]`, `org list [limits|metadata|metadata-types|users|sobject record-counts]`,
`project retrieve start|preview`, `project deploy report|preview`,
`apex get log|test`, `apex list log`, `flow get test`, `logic get test`,
`package installed list`, `package install report`, `package uninstall report`,
`package version list`, `community list template`. `apex tail log` is not a read:
it turns on debug logging (a trace flag) in the org. `org auth show-access-token`,
`show-sfdx-auth-url` and `show-user-password` print credentials and are treated
as writes. Any command not on this list counts as a write.
