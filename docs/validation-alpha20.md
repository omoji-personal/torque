# Validation for alpha 20

Alpha 20 makes Torque run on Windows and adds Antigravity as a second agent host beside
Claude Code. It also closes gate gaps found in a review of alpha 19: commands that hand a
connected session a credential, browser and desktop tools in build-only mode, a Salesforce
MCP server under a neutral name, and three checks that could pass a call when they could
not finish. This record separates what was run live, what was replayed, and what only the
tests cover.

## Observed results

Windows 11 with Python 3.13, from the source tree.

The full offline suite passed 3,739 tests and 154 subtests with 833 skips, and all 12
standalone package suites passed. The skips are tests that apply only on macOS and Linux,
plus 31 that build a symbolic link, which a standard Windows account may not create.

The workflow sync checks and the provenance check pass (53 adapters, 76 bundled files, 180
carried-over files with their original source hashes). Linux and macOS were not run by
hand for this release: the nine CI runs on the pull request (three systems, Python 3.10,
3.12 and 3.14) are the record for them. CI also builds the wheel and source distribution,
checks their contents and runs the installed smoke suite.

Run live on Windows against a Developer Edition org that holds made-up data only:

| Path | Result |
|---|---|
| `torque workspace init`, `client add --org`, `doctor` | ok |
| `torque data create` and `data update` on one record | ok. Text with `% & | < > ^ ! " $`, accents and a backslash arrived unchanged |
| `torque recover show`, `preview` and `run` for the update and the create | ok. The earlier value came back exactly, then the record was deleted |
| `torque data bulk import` (3 rows) and `data bulk update` | ok |
| Recovery of the two bulk operations | Manual by design. The saved before and after files were usable as written |
| A check-only deploy, then a deploy of one Apex class and one permission set, then an assignment | Completed, seen in the org's deployment list and setup audit trail |
| Recovery of that deploy | Not run. The session was interrupted before it |

Run with the workspace on a Google Drive for desktop volume, which has no hard links: 25
steps (client and session records, context, handoff, change records, evidence, before-state,
initiatives, lessons, workspace upgrade, the gate hook, doctor's probe), all ok.

Replayed, not live: Torque's Antigravity hook was run as a command on the hook input
Antigravity CLI 1.3.1 was seen to send, 35 made-up calls across build-only, full and
connected workspaces and 17 more on the final tree. Decisions were as designed.

## Remaining acceptance

Not verified in this release:

- Torque's hook called by a live Antigravity session, a live session loading the files
  Torque writes under `.agents/`, and what `ask` does in an interactive Antigravity session.
- Everything about connected mode under Antigravity: the launch, the binding, the call ID,
  approvals. It is tested offline only.
- `torque launch` in a real connected session on Windows.
- Seven tests added in this release run only on macOS and Linux. They have run in CI only.
- A live `sf` call for each command connected mode now refuses. The refusals are decided
  from the command text.

Known limits that remain:

- On Windows, permission and owner checks on the approval key, grants, consent evidence and
  before-state files are skipped, a launch record is not tied to a process, and tier 2
  (a separate approver account) is not available.
- Connected mode's command reader does not read a PowerShell path written with backslashes
  (`.\.venv\Scripts\torque.exe`) as a path. It then asks instead of deciding.
- A before-state deeper than the 260-character path limit is refused on Windows when long
  paths are off.
- With the gate hook on, a read-only command that names a whole initiative folder is still
  refused.
- The gate gaps listed in the alpha 19 record were not re-tested for this release.
- A command the gate cannot parse (a script, a command piped into a shell) gets a prompt,
  not a refusal. A neutral MCP server whose calls carry no Salesforce host still passes.

No package-index release and no demo readiness is claimed.

## Review process

Three read-only reviews of the repository came first: what was tied to one host, what was
tied to one operating system, and what an outside reviewer would question. Their findings
set the scope. An adversarial review of the gate changes found two defects, a flag placed
before the command that was read as a local command and a lookalike host that was blocked,
and both were fixed. These reviews supplement the measured results and do not establish
the remaining live acceptance.
