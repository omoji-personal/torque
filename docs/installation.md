# Installation and first use

Torque is currently a source-distributed development alpha. The Python package
name is `torque-salesforce`; the command is `torque`. Do not assume an index
package with that name is this unpublished build. Install your reviewed checkout
or a matching wheel supplied with its hash.

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
validation record for the specific combinations actually run, including any
Windows laptop check. The demo path must be new and outside the checkout.

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
.venv\Scripts\pip install -e .
.venv\Scripts\torque doctor
.venv\Scripts\torque demo C:\Work\torque-demo
```

The demo path must be new and outside the checkout, same as on macOS/Linux. Put
`.venv\Scripts` on PATH, or use the absolute path, the same way `.venv/bin` is used
above.

The de-identified-mode hook (see `ai-access.md`) goes in the workspace's own
`.claude/settings.json`, never a user-level settings file. On Windows, do not use a
bare `python` in the hook command: it often resolves to the Microsoft Store alias or
to an interpreter without Torque installed. The hook then fails with an exit code
other than 2, and Claude Code treats that as a non-blocking error, so the tool runs
and build-only mode is silently off. Always point the hook at the venv interpreter by
absolute path, written with forward slashes so it survives Git Bash (which Claude Code
uses for hooks when installed) as well as cmd:

```json
{"hooks": {"PreToolUse": [{"matcher": "Bash|Read|Edit|Write|MultiEdit|NotebookEdit|Grep|Glob",
  "hooks": [{"type": "command", "command": "C:/Work/torque/.venv/Scripts/python.exe -m torque.gate"}]}]}}
```

Check it once after wiring, with build-only mode set, from Git Bash in the workspace
directory, using the exact command from the hook:

```sh
echo '{"tool_name":"Read","tool_input":{"file_path":"clients/example/notes.md"},"cwd":"."}' | C:/Work/torque/.venv/Scripts/python.exe -m torque.gate; echo "exit=$?"
```

It must print `De-identified mode: client context stays out of the AI session.` and
`exit=2`. Any exit other than 0 or 2 (for example `No module named torque`, exit 1)
means the gate is not running and nothing is being blocked; fix the interpreter path.

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
changes and evidence. Missing or changed evidence is reported for review; local
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
updates. Root instructions and client content are not automatically rewritten.
Uninstall with the installer you used (`pipx uninstall torque-salesforce` or the
installation's `python -m pip uninstall torque-salesforce`). Private workspaces
remain on disk for your review and retention needs.
