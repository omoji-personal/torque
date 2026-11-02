# Installation and first use

Torque is currently a source-distributed development alpha. The Python package
name is `torque-salesforce`; the command is `torque`. Do not assume an index
package with that name is this unpublished build. Install a checkout of a release
tag (or of the commit you reviewed), or a release wheel, checked against its
checksums when the release publishes them ([verify a release wheel](#verify-a-release-wheel)).

## Isolated checkout installation

From the Torque source directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/torque --version
.venv/bin/torque doctor
.venv/bin/torque demo ../torque-demo
```

Python 3.10+ is required. The core dependency is PyYAML. macOS, Linux, and Windows
are all exercised by the CI matrix (`.github/workflows/validate.yml`); see the
[alpha 21 validation record](validation-alpha21.md) for the combinations actually
run, and the [alpha 20 record](validation-alpha20.md) for what Windows does not yet cover.
The demo path must be new and outside the checkout.

Use the absolute `.venv/bin/torque` path from any working directory, or put that
installation's `.venv/bin` on the PATH of the process running your assistant.
Verify `torque --version` and `torque doctor` inside that assistant's terminal.
Doctor shows the loaded package directory; its JSON output also identifies the
Python executable. This helps distinguish a reviewed installation from an older
copy elsewhere on PATH. Activating a virtual
environment in a different shell does not update an already running desktop app.
Do not copy the executable alone: it points to the installation's interpreter.

An alternative is [pipx](https://pipx.pypa.io/stable/), which installs command-line
applications into isolated environments and exposes their executables on PATH.
After installing pipx using its official platform instructions, run from the
reviewed Torque checkout:

```sh
pipx install .
pipx ensurepath
```

Open a new terminal or restart the assistant if needed, then verify
`torque --version`. If Torque was previously installed, inspect `pipx list` before
replacing it. A wheel can be used instead of `.`. No administrator pip install is
necessary.

### Verify a release wheel

CI writes a `SHA256SUMS` file for every candidate build. When a GitHub release lists
those checksums for its wheel and source archive, in its notes or as an attached
`SHA256SUMS`, download the files into one folder and compare before installing:

```sh
sha256sum -c SHA256SUMS            # Linux
shasum -a 256 -c SHA256SUMS        # macOS
```

```powershell
Get-FileHash .\torque_salesforce-*.whl -Algorithm SHA256
```

On Windows, compare the printed hash with the wheel's line in `SHA256SUMS`. A matching
checksum shows the file is the one published; it does not by itself show which commit
or build produced it, and releases are not yet attested. When a release lists no
checksums, install from its tagged checkout instead.

## Windows

From PowerShell:

```powershell
winget install --scope user Python.Python.3.12
winget install --scope user Git.Git
```

`--scope user` avoids an administrator prompt and keeps the install out of Program
Files. Open a new terminal afterward so the updated PATH takes effect, then from the
Torque source directory:

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\torque doctor
.venv\Scripts\torque demo C:\Work\torque-demo
```

The demo path must be new and outside the checkout, same as on macOS/Linux. Put
`.venv\Scripts` on PATH, or use the absolute path, the same way `.venv/bin` is used
above.

Smart App Control can block the unsigned `torque.exe` launcher that pip writes
([alpha 21 record](validation-alpha21.md)). Then run the same commands through that
environment's interpreter, `.venv\Scripts\python.exe -m torque doctor`; it runs the same
code. Name that interpreter by its path: a bare `py -m torque` can start a Python that does
not have Torque installed. The gate hook already runs through Python.

What Torque does for Windows itself:

- The Salesforce CLI is installed as `sf.cmd` (npm, or the Salesforce installer), which
  Windows does not start for the bare name `sf`. Torque finds it on PATH (never in the
  current folder), starts an npm shim as its own `node.exe` script, and runs any other
  batch file through `cmd.exe` with each argument quoted so that `&`, `%`, `^` and `"`
  stay literal. The same applies to `gemini`.
- Output that is redirected (a pipe, a file, an assistant's terminal) is written as
  UTF-8, not in the ANSI code page, unless `PYTHONIOENCODING` is set. Output to a
  console window is unchanged. A script that reads Torque's output should decode it as
  UTF-8 (in Python, `encoding="utf-8"`), or set `PYTHONIOENCODING` for the call.
- `torque recover run` starts its child with the real interpreter when Torque runs in a
  virtual environment, because the environment's `python.exe` is a launcher.
- `torque launch` (connected mode) starts `claude` as a child process and waits for it,
  returning its exit code. Windows has no `exec`: the call used elsewhere would end
  Torque and hand the console back to the shell while the agent was still reading it,
  and could not start `claude` installed as a batch file. Ctrl+C goes to the agent.
- A command Torque prints for you to paste (`show_command` in `torque context`, the grant
  command `torque approval request` prints) is quoted so that PowerShell and cmd.exe read the
  same words: a word with a space or a character either shell reads as its own (an
  apostrophe, `&`, `;`, `(`) is in double quotes. A word with `$`, a backtick, `%` or `!` is in
  PowerShell's single quotes, which only PowerShell reads.
- The build-only gate reads PowerShell commands as PowerShell, and runs as an
  Antigravity hook as well as a Claude Code hook: [build-only mode](ai-access.md).

Limits on Windows: a path of 260 characters or more fails unless long paths are enabled
in Windows, and evidence folders are deep, so keep the workspace path short. A
before-state that would pass the limit is refused before anything is copied, with the
number of characters it is over. Creating a symbolic link needs Developer Mode or an
elevated prompt; Torque itself creates none, and the tests that build one are reported as
skipped there.
On a volume without hard links, such as Google Drive for desktop, new files are published
another way (see [workspace upgrades](workspace-upgrades.md#interrupted-or-simultaneous-updates));
the automated tests replace the link call and do not run on such a volume.

The build-only-mode hook (see `ai-access.md`) goes in the workspace's own
`.claude/settings.json`, never a user-level settings file. On Windows, do not use a
bare `python` in the hook command: it often resolves to the Microsoft Store alias or
to an interpreter without Torque installed. The hook then fails with an exit code
other than 2, and Claude Code treats that as a non-blocking error, so the tool runs
and build-only mode is silently off. Always point the hook at the venv interpreter by
absolute path. `torque workspace ai-access build-only` writes the entry in exec form:
`command` is the interpreter and `args` its arguments, which Claude Code spawns with no
shell, so no quoting is involved and Git Bash is not needed:

```json
{"hooks": {"PreToolUse": [{"matcher": ".*",
  "hooks": [{"type": "command", "command": "C:/Work/torque/.venv/Scripts/python.exe", "args": ["-I", "-c", "import os,sys;sys.excepthook=lambda t,e,b:(print('(Torque gate could not load: '+t.__name__+': '+str(e)+'; call blocked)',file=sys.stderr,flush=True),os._exit(2));from torque.gate import main;sys.exit(main())"], "timeout": 600, "onFailure": "block"}]}]}}
```

An entry written by hand in the older shell form still works; on Windows it needs Git
Bash, which Claude Code uses for shell-form hooks when installed, and its interpreter
path is written with forward slashes so it survives Git Bash as well as cmd:

```json
{"hooks": {"PreToolUse": [{"matcher": ".*",
  "hooks": [{"type": "command", "command": "\"C:/Work/torque/.venv/Scripts/python.exe\" -I -c \"import os,sys;sys.excepthook=lambda t,e,b:(print('(Torque gate could not load: '+t.__name__+': '+str(e)+'; call blocked. In your own terminal, run torque doctor --workspace W, with W the workspace folder, to see why.)',file=sys.stderr,flush=True),os._exit(2));from torque.gate import main;sys.exit(main())\"", "timeout": 600}]}]}}
```

The `sys.excepthook` wrapper makes the hook exit 2 (block) if that interpreter cannot
import Torque, instead of exit 1, which Claude Code would treat as non-blocking. `-I`
(isolated mode) keeps the working directory off Python's import path, so a `torque`
folder written into the workspace cannot replace the gate. The `.*` matcher sends every
tool call to the gate, which blocks tools it does not recognise. `"timeout": 600` keeps
Claude Code's default hook timeout explicit: a timed-out hook lets the call proceed as if
it were allowed, so do not lower it (doctor warns when it is missing or larger). The gate
has its own 5-second budget per call and blocks when it runs out, well inside that
timeout. The wrapper cannot
help if the interpreter path itself is wrong. Check the wiring once after setup,
and after every update, with build-only mode set:

```sh
C:/Work/torque/.venv/Scripts/torque doctor --workspace C:/Work/firm-workspace
```

It must report `AI access: build-only (hook command blocked a standalone probe; ...)`.
`HOOK NOT IN FORCE` means the hook is missing or did not block a synthetic client-path
probe; doctor prints the command to use and exits 3. It also exits 3 when the hook runs
without `-I`, its matcher is narrower than `.*`, or `disableAllHooks` switches hooks off.
The probe runs the hook command directly, so also confirm once, in a real session, that
a `Read` of a `clients/` file is blocked. Doctor runs an exec-form entry's argument vector
with no shell; on Windows it runs a shell-form entry through Git Bash, as Claude Code does,
when Git Bash is installed (or named by `CLAUDE_CODE_GIT_BASH_PATH`).

## Optional capabilities

| Capability | Additional requirement | Local inspection |
| --- | --- | --- |
| Context, records, demo, handoff | Core installation only | `torque doctor` |
| Live Salesforce operations | Official Salesforce CLI and explicitly selected authenticated org | `torque doctor --for salesforce` |
| Browser tests | `browser` extra and Playwright Chromium or an explicitly configured authenticated browser | `torque doctor --for browser` |
| Meeting image preparation | `meeting` extra and ffmpeg | `torque doctor --for meeting` |
| Model-assisted meeting/vision/prompt interpretation | Separately configured provider CLI/account and appropriate inputs | Check the chosen adapter's help and provider configuration |

For a virtual-environment installation, from the reviewed checkout:

```sh
.venv/bin/python -m pip install '.[browser,meeting]'
.venv/bin/python -m playwright install chromium
```

Linux browser runtimes may need OS libraries; follow the installed Playwright
version's diagnostics. The browser extra's importability does not prove that a
browser can launch, authenticate, switch users or exercise the client's workflow.
The doctor command deliberately reports local dependencies separately from live
verification. It also reports private paths already tracked by Git when inspecting
a workspace; it does not rewrite Git history or change repository visibility.
With `--workspace PATH --client NAME`, it also checks that client's saved sessions,
changes and evidence (doctor has no `--initiative` option yet). Missing or changed evidence is reported for review; local
dependency readiness does not certify saved claims or live Salesforce behavior.

Install and authenticate [Salesforce CLI](https://developer.salesforce.com/docs/platform/salesforce-cli-reference/guide/cli_reference.html)
using its official instructions. Torque reuses that connection. Record the alias
in a client config and still supply the actual target to live operations.

Torque is open source and needs no Torque subscription. Salesforce environments,
your coding assistant, model providers and third-party delivery products have their
own accounts, terms and possible charges. No paid provider is required by the demo.

## Upgrade or remove

Install the reviewed new package into the same isolated environment. Then run:

```sh
torque workspace upgrade /path/to/private-workspace --check --json
torque workspace upgrade /path/to/private-workspace --json
```

See [upgrade semantics](workspace-upgrades.md) for local edits and interrupted
updates. Root instructions, client and initiative content are not automatically
rewritten. Upgrading to 2.0.0a19 needs no record migration: existing clients keep
their folders, and `torque initiative add` creates `initiatives/` on first use.
Uninstall with the installer you used (`pipx uninstall torque-salesforce` or the
installation's `python -m pip uninstall torque-salesforce`). Private workspaces
remain on disk for your review and retention needs.
