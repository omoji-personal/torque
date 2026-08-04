# Toolchain

Torque validates external tools by **supported range + capability probe**, never bare
version equality — a firm's patch-version drift must not brick the workspace. The one
exception: the MCP server package is **version-locked** (its tool inventory defines gate
matchers).

| Tool | Supported | Probe |
|---|---|---|
| sf CLI | >= 2.60 | `sf commands --json` parses; write-surface derivation succeeds |
| Python | >= 3.8 | stdlib only; `python3 -c "import json,hashlib"` |
| git | >= 2.40 | `git rev-list --objects --all` and `cat-file` behave |
| Claude Code | >= 2.x | `claude -p` headless probe returns the rules token |
| @salesforce/mcp | LOCKED (recorded on P1 pin) | tool inventory derivation |
| Playwright | >= 1.55 | chromium binary present; real render probe |

## Enforcement platforms: macOS and Linux. Windows is NOT supported.

P1-010, and it was a documentation gap rather than a control gap — the behaviour was always this,
and nothing outside a comment in `hooks/lib.py` said so.

The gate's hard deadline is `signal.SIGALRM` + `setitimer`. Where SIGALRM is unavailable — Windows,
or a non-main thread — `_arm_deadline` returns quietly and the only remaining bound is the
per-callout clamp. That matters more than it sounds: a hook the host kills for overrunning exits
non-2, and **non-2 ALLOWS**. So on a platform without SIGALRM the fail-closed timeout guarantee
degrades to a fail-OPEN one, silently, and `budget_fits_hook_timeout` cannot see it because it
runs where SIGALRM exists.

This is stated rather than fixed, deliberately. A watchdog process or a worker-with-hard-kill
would close it, and both are substantial machinery for a platform the tool is not delivered on:
Torque runs inside Claude Code against the `sf` CLI, and the non-main-thread branch is
unreachable for a hook that runs as a subprocess. Naming the boundary is the honest move; building
a watchdog for it would be building for a user who does not exist.

CI runs `ubuntu-latest` only, so the guarantee is currently tested on one of the two supported
platforms. The other is the one most operators use.

The Python floor is 3.8 because that is what the code actually needs, and it is not taken on
trust: `python_floor_is_real` re-parses every source file under each candidate interpreter and
also scans for stdlib introduced later (`removeprefix`, `zoneinfo`, `tomllib` …), so reaching for
a newer call raises the floor instead of letting this table drift. It reads the floor out of
README.md, which is why this table previously said 3.11 against README's 3.8 and `torque-init`'s
enforced 3.8 without any check noticing: the contradiction was between two documents, and only
one of them was being read.

**Observed versions** are recorded by `bin/torque-attest` into the `toolchain` field of each
attestation (sf, node, python) on attested runs. They are not printed into the run header, and
there is no preflight stage — `validate.py` has no such phase.

**The Bash write surface is enumerated in code**, not derived: the sf subcommand classification
lives in the gates and the surrounding vocabularies in `hooks/shellparse.py`
(`GIT_WRITE_SUBS`, `OPAQUE_WRITERS`, `MCP_WRITE_LEADS`, `_WRITE_VOCAB`). Deriving it from
`sf commands --json` and failing the harness on drift is a good idea and remains unbuilt; until
it is built, a subcommand Salesforce adds is not automatically classified, which is a real gap
and is why the gates default-deny anything they cannot resolve. There is no
`harness/checks/cli-write-surface.json` — the file has never existed. Its basename is already
registered in the protected lists so that the derivation, if it is built, cannot be authored by
the agent whose reach it defines.
