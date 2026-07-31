# Toolchain

Torque validates external tools by **supported range + capability probe**, never bare
version equality — a firm's patch-version drift must not brick the workspace. The one
exception: the MCP server package is **version-locked** (its tool inventory defines gate
matchers).

| Tool | Supported | Probe |
|---|---|---|
| sf CLI | >= 2.60 | `sf commands --json` parses; write-surface derivation succeeds |
| Python | >= 3.11 | stdlib only; `python3 -c "import json,hashlib"` |
| git | >= 2.40 | `git rev-list --objects --all` and `cat-file` behave |
| Claude Code | >= 2.x | `claude -p` headless probe returns the rules token |
| @salesforce/mcp | LOCKED (recorded on P1 pin) | tool inventory derivation |
| Playwright | >= 1.55 | chromium binary present; real render probe |

Preflight records observed versions into every run header. The Bash write surface is
re-derived from `sf commands --json` at every preflight; drift FAILS the harness (not the
hook) and the committed `harness/checks/cli-write-surface.json` is updated deliberately.
