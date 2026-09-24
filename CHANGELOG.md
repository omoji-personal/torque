# Changelog

## 2.0.0a15 - connected mode with per-write approval, 2026-09-24 (not published to a package index)

An opt-in third `ai_access` mode for stage-2 work: an AI session works in one client's
orgs, and each org write waits for the consultant's approval of that exact command. The
default (`full`) and build-only behavior are unchanged.

- `ai_access: "connected"` is valid only with `"approval": "required"`; anything else is
  build-only, and an older Torque reads it as build-only. The strictest mode wins when
  workspaces nest: build-only, then connected, then full. `torque workspace ai-access
  connected --approval required` needs a person at a real terminal outside the session.
- `torque launch` binds a session to one client. Reads of that client's approved orgs are
  allowed; check-only deploys and test runs are allowed and logged; other clients' folders,
  orgs outside the consent and `sf` calls without an explicit org are refused.
- `torque client consent record|sign-off|suspend|show` keeps the client's written agreement,
  its approved orgs (with their live 18-character IDs) and data classes, and a second
  reviewer's sign-off. Org access needs active, signed-off consent.
- `torque approval request|grant|deny|status|list|log|require`: the session requests an
  approval for one exact command, MCP call or browser window; the consultant grants it at a
  real terminal after reading the command, org, components, before-state, namespaces and file
  digest, and types back a code; the gate consumes it once. It binds the command, the files
  it deploys, the working folder, the org and its ID, the client, the change and a 15-minute
  window (30 for a browser window). Every step is a change-record event.
- Production approvals need an independent before-state (imported, or retrieved or read now
  as its own step) covering each named component, or a written recovery path.
- Two approval tiers: tier 1 signs approvals with a key in the consultant's home (a script
  the session runs can read it and forge an approval, as documented); tier 2, recommended,
  accepts only approvals owned by a separate approver OS account.
- Programs the gate cannot check make the host ask the consultant in the `default`,
  `acceptEdits` and `plan` permission modes (or when the host sends no mode), and are refused
  in any other mode. Approval administration, `sf alias set`, `sf config set` and desktop
  control are refused. The Salesforce CLI's credential, alias and configuration folders and
  its installation are guarded.
- `torque deploy|data|org|recover` re-check the consumed approval and the live org ID when
  they run in a connected workspace.
- `torque approval permissions [--write]` generates Claude Code permission rules (ask on
  write routes and interpreters, deny approval administration, bypass mode disabled), and
  `torque doctor` checks them, the hook, the tier and the consent, and runs five synthetic
  calls through the hook (`--live` also compares each org's ID with the consent).
- The rule file `production-approval.md` ("propose, show the plan and stop" for production)
  is copied into a connected workspace; `delivery-practice.md` gains one line pointing to it.
- After the R1 review: an approval is unusable once its change record is gone or the consent
  no longer names its org ID; the gate uses an approval only after every other part of the
  call is allowed; an `sf` read without an org flag (`sf org display`) is refused; a
  production browser window needs a written recovery path; the grant screen escapes
  non-printable characters.
- After the R2 review: the file binding covers every payload flag value (legacy spellings and
  comma lists included), a tree-import plan's data files and an MCP call's files, and refuses
  missing files and links; a browser window is bound to the org the browser navigated to;
  record and log reads in legacy, REST and MCP form need their consent class; consent needs a
  dated sign-off by the right client for every use, scripts included; `torque client list`
  and any route naming an org outside the consent are refused; skipped prompts refuse writes
  and browser changes too, and `sh -c` asks; a before-state is checked for content-level
  coverage (source folders included), the org it was captured from and its order, and a
  capture checks the org first; Torque-route wrappers find connected mode themselves, refuse
  an unreadable configuration and verify the approval again; an approval is returned after
  the wrapper could not resolve the org; recording each use is mandatory; large payloads go
  through the wrapper route; the grant screen reads the check-only result and can compare a
  Setup Audit Trail export; every owner step asks for a typed code; tier 2 requires a
  separate account and an approver-owned folder and is refused on Windows; the permission
  rules cover interpreter families, `torque ai-regression` and the platform's key path, and
  flag overlapping wildcard allows; doctor reads the user, project and local settings,
  requires the fail-closed hook and runs bound probes; consent, approval and key files are
  guarded in every mode where the hook runs.
- After the R2 recheck: a deploy with no selector binds every package folder, components in
  shared files (custom labels, workflows, sharing rules) bind that file, a named component
  with no local file is refused, and `--flag=a b` reads both values; a browser change is
  bound to the exact tab (server and tab ID) and needs that tab to have shown only the
  window's org; REST paths are decoded before classification and `sf data resume` needs the
  records class; destructive manifests are in the recovery scope and an object or bundle
  needs its definition file; wrappers refuse an indeterminate workspace state and a different
  working folder, claim atomically, and a revert child that cannot resolve its org returns
  the parent approval; approval events have enforced schemas, denials keep their recovery
  and validation references, and the log marks unlinked observations; permission overlap is
  exact glob intersection; record exports become record evidence; the full-mode guard
  resolves paths and covers folders that hold the records; doctor probes an unbound
  check-only call; MCP writes without files can be granted; both capture spellings are org
  reads and consent is checked for the right client.
- After the R2 recheck of round 3: in connected mode only Torque's own browser (`torque
  browser --target-org ORG`) changes an org. It checks the org ID and a granted window when
  it starts, and every request's actual origin while it runs. Browser MCP and devtools tools
  may only read and navigate. Bundles bind every file in their folder; Apex, Lightning
  component and static resource recovery needs every file it restores; recovery approvals
  bind their snapshot and show the recovery command; destructive manifests are bound as
  files and their deletions checked against the before-state without needing local source;
  the full-mode guard follows `cd` within a command.
- After the final recheck: Torque's browser is launched so other orgs' hosts do not
  resolve (redirect hops included), without a proxy or service workers, and an attached
  operator browser is refused in connected mode; the session stops, closing its pages and
  context, when its window ends or the consent is suspended; a recovery approval binds the
  snapshot folder and the exact operation, read with the recovery's own parser, and the run
  must match them.
- After the targeted recheck: the browser's allowed Salesforce hosts are exact names (no
  wildcard that could admit a sandbox or another org), and every write-capable request
  reads the window and consent again, with no cache.
- After the last targeted rechecks: the login hosts are no longer resolver exceptions (the
  session starts through frontdoor on the org's My Domain), so a 307 or 308 POST redirect
  to them, or to another org, fails to resolve; the only other exception is the static
  content host, which the route handler allows for reads only. The browser handles every
  request and redirect itself (Torque never replays one, so no header or body can be
  resent to another origin), and every request the handler sees, reads included, reads
  the window and consent again, with no cache.
- Documentation: [connected mode](docs/connected-approval.md), with the host facts it relies on,
  what it stops and what it cannot stop; [build-only mode](docs/ai-access.md) lists the three
  modes; the [alpha 15 record](docs/validation-alpha15.md).

## 2.0.0a14 - long commands in build-only mode, 2026-09-24 (not published to a package index)

A spot-check of the released alpha 13 found that one regular-expression call could hold
the gate past its watchdog. The watchdog is a thread, and a pattern match does not let
it run until the match returns. The brace-expansion pattern took time that grew with the
square of a command's length, and nothing limited that length: a 120,000-character
command held the hook for 83.5 seconds, and a longer one could outrun the host's hook
timeout, which lets the call run.

- The gate blocks any command, path or other argument it parses that is longer than
  20,000 characters (`MAX_INPUT_CHARS`), before any pattern runs, with a message that says
  so. A 100,000-character command now blocks in well under a second. The contents a file
  tool writes (`Write`, `Edit`, `MultiEdit`, `NotebookEdit`) are not parsed and not limited.
- Brace expansion (`prefix{a,b}suffix`) takes linear time and gives the same result as
  before. An expansion that would grow a command past 80,000 characters is blocked.
- A 5,000-character commit message is still allowed.
- The README describes Torque in one paragraph, states the status as development alpha
  2.0.0a14, and names the install-and-demo section as such.
- [Build-only mode](docs/ai-access.md) states the length limit; the
  [alpha 13 record](docs/validation-alpha13.md) records the gap and that alpha 14 closes it.

## 2.0.0a13 - build-only mode follow-up, 2026-09-24 (tagged; not published to a package index)

A later review round found ordinary shell routes around build-only mode that the
documentation implied were covered. This release closes them and brings the alpha
12 record up to date.

- Making a link that leads out of the tree is blocked: `ln`/`ln -s`, `cp -s`, `mklink` and
  `New-Item -ItemType SymbolicLink|Junction|HardLink` whose target resolves to `clients/`,
  into it, to the workspace root or to any folder above it. A path that names a link is
  still resolved.
- `torque doctor` scans the workspace once (outside `clients/`, skipping `node_modules` and
  `.git`, up to 200,000 entries) for links that resolve to `clients/` or above it, and
  reports them, or an incomplete scan, as not ready. The gate does no filesystem walk per
  call; a link that already exists and a recursive tool that follows links are listed as a
  limit.
- A word longer than any file name (a long commit message, a long `echo` chain) is no
  longer read as a path that fails the whole call closed.
- The hook examples set `"timeout": 600`, and the documentation says a timed-out hook lets
  the call proceed. `torque doctor` warns when the hook entry has no timeout or a larger
  one.
- Each gate call has a 5-second time budget for path resolution, glob expansion and its
  git queries, and a budget of 10,000 glob matches (each distinct pattern counted once);
  past either it blocks with a message saying so, rather than letting the host's hook
  timeout allow the call. A watchdog in the hook process enforces the time budget even
  inside a step that does not check it, and exits 2.
- `unzip` option clusters are read like getopt: `-od..` and `-qod ..` name the extraction
  folder.
- Doctor's link scan follows a link to a folder outside the workspace, so a link that
  reaches `clients/` through an outside folder is reported.
- A path or search root holding an unresolved `$` expansion, including one set earlier
  in the same command (`R=..; rg x $R`, `"${PWD%/project}"`), is read as each folder up
  to the workspace root, the rule `cd "$DIR"` already followed.
- A `cd` inside `if`, `then`, `else`, `elif`, `while`, `until` or `for ... do`, or in an
  `if` condition, changes the directory later paths are checked from.
- `grep -d recurse`, `--directories=recurse` and abbreviations such as `--recur` count as
  recursive searches.
- `git status --ignored` and `git ls-files -o`/`-i`/`--others`/`--ignored` are blocked
  when their scope reaches `clients/`.
- A `Glob` pattern that is absolute or climbs with `..` is read as rooted where it leads.
- Short-option clusters are parsed per tool: digits (`zip -9r`, `grep -r2`) may appear
  anywhere, and an option that takes a value ends the cluster (`grep -rA2`), so these
  still count as recursive.
- `torque doctor`, the gate's own description and the alpha 12 record state the final
  rule while client files are tracked or staged: only `git status` without
  `-v`/`--verbose` and `git rm --cached` of paths under `clients/` pass.
- Patches and archives that could write outside `project/` are blocked: `git apply`
  outside `project/` (unless `--directory` keeps it there), `git am` unless the
  repository's top is inside `project/`, `patch` outside `project/` or from a pipe, a
  patch file naming an absolute path or `..`, and `tar`/`bsdtar`/`unzip`/`ditto -x`
  extraction at or above the workspace root, into `clients/`, or with absolute or
  rewritten member names.
- The 2.0.0a12 and 2.0.0a13 headings say "tagged; not published to a package index"
  rather than "unpublished".
- The alpha 12 validation record's "Review scope" now records its scoped security
  re-review, the three spot-checks and the final re-run at d3b0891.
- [Build-only mode](docs/ai-access.md) lists the new checks, the new over-blocks and
  what a variable can still hide.

## 2.0.0a12 - build-only mode follow-up, 2026-09-23 (tagged; not published to a package index)

A spot-check of alpha 11's host-tool change and a further review round found
routes around the mode alpha 11 called de-identified mode. This release closes
them and renames the mode.

- The mode is now called build-only mode in the documentation, the CLI and the
  gate's messages, matching its `build-only` setting. It redacts nothing; the
  documentation says so. `ai_access` and its values are unchanged.

- `EnterWorktree` may only enter a worktree under `.claude/worktrees/`, never its
  `clients/`. Creating a new worktree (`name`, or no arguments) is blocked when
  `clients/` has files tracked in git, when `.worktreeinclude` names or matches
  files in `clients/`, or when git cannot answer. `.worktreeinclude` is now a
  guarded file like `workspace.json`.
- Each worktree under `.claude/worktrees/` is checked as a workspace of its own,
  so a copy of `clients/` there is guarded like `clients/`.
- `LSP` is limited to `documentSymbol`, `hover` and `goToDefinition` on a named
  `filePath` outside `clients/`. Workspace-wide operations (`workspaceSymbol`,
  `findReferences` and the rest) and unknown operations are blocked, a
  `file://` URI is read as its path, and every string argument is checked.
- `git clean` without `-n`/`--dry-run`, and `git stash` with `-u`, `-a`,
  `--include-untracked` or `--all`, are blocked at or above `clients/`,
  `.claude/` or the hook's environment, as are `git stash show -u` and a stash's
  untracked parent (`stash^3`).
- MCP tools that run a command (a `command`, `cmd` or `script` argument) get the
  Bash scan, as do other tools with a `cmd` or `script` string.
- Search parsing reads attached patterns (`-eERROR`, `grep -rneERROR`), grep's
  `--regexp` abbreviations and modes without a pattern (`rg --files`, `ack -f`),
  so a following `..` is read as a path. `tar` follows its `-C` and
  `--directory` changes in order, reads old-style key bundles (`tar cCf .. - .`) as
  tar does, and `bsdtar`, `gtar` and `gnutar` are treated as tar.
- Client files are kept out of git where they would enter it: `git add` or
  `git stage` (forced or not) reaching `clients/`, `.claude/` or the hook's
  environment, `git update-index --add` and `git hash-object -w` on those paths.
  A file value attached to `-f`/`-F` (`git commit -Fclients/...`) is checked as a
  path. git's own programs run by path and git pointed outside `project/` (`-C`,
  `--git-dir`, `--work-tree`, `GIT_DIR`) are blocked. While any client file is in
  git's index, or git cannot be checked, only `git status` and `git rm --cached`
  of `clients/` pass, and `torque doctor` reports the count (or "unknown") and
  fails. Other ways git can read what it already holds are listed as limits.
- `EnterWorktree` by path into an existing worktree works when the worktree tracks
  `workspace.json`.
- A recursive search, archive or clean rooted at the filesystem root (`grep -r x /`,
  `tar -C / ...`) now counts as reaching `clients/`; on Linux and Windows it did not.
- `file:` URIs are parsed as URIs, so `file://localhost/...` and `file:/...`
  name the path they point to.
- The `torque` command rejects abbreviated options, and the gate blocks
  abbreviations of `torque doctor --client` (`--clie`).
- The four playbooks the demo uses have an "In build-only mode" section: the
  agent works from material the consultant supplies with names, IDs and values
  removed, and every live-org, record or client-session step is a hand-off.
- The README says the `qa-token-*` catalogue entries manage legacy QA skip
  records only.
- [Build-only mode](docs/ai-access.md) lists `SendMessage` to a peer session,
  what a language server returns, and the remaining git routes as limits.
- The alpha 11 validation record's "Review scope" now records its security
  review, two re-reviews and the spot-check.

1835 offline tests pass (154 subtests). See the
[alpha 12 validation record](docs/validation-alpha12.md).

## 2.0.0a11 - unpublished de-identified mode hardening, 2026-09-23

A four-model review of alpha 10's de-identified mode found routes an assistant can
take in ordinary use. This release closes the reported routes and states the
mode's limits. A security re-review of the first alpha 11 candidate found more;
the last group of items below closes those.

- Paths after a `cd` or `pushd` earlier in the same command, attached
  redirections (`<file`, `2>file`), `--flag=path` and `NAME=path` values, globs
  expanded against the disk, brace expansion and `$PWD` are now resolved before
  the `clients/` check. `**`, `tar`, and `zip`, `cp`, `scp` or `rsync` with a
  recursive flag are treated as recursive reads.
- MCP tool calls are checked for path arguments, not only for Salesforce names:
  a string argument that resolves into `clients/`, the hook configuration or the
  installed Torque package is blocked, as is a tree-walking MCP tool rooted at or
  above `clients/`.
- `git grep --untracked` and `git grep --no-index` rooted at or above `clients/`
  are blocked. Plain `git grep` still passes.
- An explicit `null` or empty `ai_access` now means build-only. Every build-only
  workspace from the session's directory upward applies, so a nested
  `workspace.json` cannot downgrade the workspace above it.
- Edits to the installed Torque package by the file tools or a Bash command that
  names it, and `pip`, `uv` or `pipx` commands that name Torque (uninstall,
  reinstall, downgrade), are blocked. Scripts and unnamed installs
  (`pip install -r`) are not; see the limits in the docs.
- New fail-closed hook command: it exits 2 (block) instead of 1 when the hook's
  interpreter cannot import Torque. `torque doctor --workspace` now reports the
  mode, runs the wired hook on a synthetic probe in build-only mode, and exits 3
  with the fix when the hook is missing or does not block.
- `sf code-analyzer run`, `sf code-analyzer rules` and `sf project convert` now
  work in build-only mode when no org flag is given and their roots stay away
  from `clients/`.
- The alert-triage demo's client note is a draft to send only after the likely
  cause is confirmed. The demo guide describes all four synthetic scenarios.
- [De-identified mode](docs/ai-access.md) now opens with its current limits: no
  org allowlist, no metadata-only mode, an unauthenticated setting, one guarded
  folder, the session running as the user, and no redaction of pasted text.

Closed after the security re-review:

- A `cd`, `pushd` or `popd` whose target the gate cannot know (`cd -`, `cd ~-`,
  `popd`, `cd "$OLDPWD"`, `cd "$(git rev-parse --show-toplevel)"`) no longer
  narrows the directories a later path is checked from. The rest of the command
  is checked from every directory seen, the workspace root, the folders between
  and the root's parents.
- The hook command runs Python with `-I`, so a `torque/` folder or
  `sitecustomize.py` written into the workspace cannot replace the gate. Writes
  of a `torque/` folder, `torque.py`, `.pth` files and `sitecustomize.py` or
  `usercustomize.py` anywhere in the workspace, and into the hook interpreter's
  site-packages, binary and virtual environment, are blocked. Doctor exits 3 for
  a hook without `-I`.
- Tools other than Bash that run a command string (Claude Code's `Monitor`, a
  `PowerShell` tool) get the Bash scan, and tools the gate does not recognise are
  blocked. The documented hook matcher is now `.*`; doctor exits 3 for a
  narrower one.
- On Windows, Git Bash drive paths (`/c/...`, `/cygdrive/c/...`) are resolved to
  their drive, and doctor runs its probe through Git Bash when installed, as
  Claude Code does.
- The session's project directory (`CLAUDE_PROJECT_DIR`) and every path a call
  names now also select the governing workspace, so `cd ..` out of the workspace
  no longer ends its gating.
- Redirections glued to a word (`cat<file`), search options with values
  (`rg -g '*.md'`, `grep -r -A 2`), `git grep -- PATTERN`, `command cd`,
  `time cd`, `env -C DIR`, `sf project convert` rooted at or above `clients/`
  (or with no root), and curl's `@file` forms are handled.
- Deleting, moving or recreating the environment that holds the gate
  (`rm -rf .venv`, `python -m venv --clear`) is blocked. Doctor reads matchers
  as regular expressions, fails when `disableAllHooks` switches hooks off, and
  says its probe ran the hook command, not the host.
- Git's abbreviated long options (`--untr`, `--no-ind`), `git diff --no-index`,
  `diff -r`, ANSI-C quoting (`$'\x63lients'`), zsh glob groups (`c(l)ients`) and
  comma-less brace groups are handled. Doctor names a hook that could not load
  the gate instead of reporting "exit 2", and a glob at the root names the glob
  in its block message.

1488 offline tests pass (154 subtests). See the
[alpha 11 validation record](docs/validation-alpha11.md).

## 2.0.0a10 - unpublished de-identified mode, Windows and demo breadth update, 2026-09-23

- Every forwarded command (data, deploy, revert, QA, logs, probes, advisory, and
  the rest) now identifies itself as `torque` in its usage and help text and in
  its own descriptions, instead of the tool name it continues from.
- Add a de-identified mode: `torque workspace ai-access build-only` plus a
  Claude Code PreToolUse hook (`python -m torque.gate`) that blocks Salesforce
  org access, client context and workspace-mode tampering while the mode is on.
  The gate resolves paths, unwraps shell wrappers and quoting, also covers
  Torque's own legacy scripts (`jsc`, `jsc-qa` and the rest), `python -m` forms
  and MCP tools whose names indicate Salesforce access, and fails closed on
  evaluation errors and unreadable configuration; it is a best-effort assistant
  guard, not a sandbox. See [de-identified mode](docs/ai-access.md).
- Ship four synthetic offline demo scenarios with matching workflow recipes:
  alert triage, gift and payments, Outbound Funds grants, and requirements to
  build. Each scenario is clearly marked synthetic and ships under the demo
  client's `examples/`.
- Add Windows to the CI matrix alongside macOS and Linux, and add a Windows
  section to the [installation guide](docs/installation.md).
- Fix Windows-only defects the matrix found: `HOME` substitution and backslash
  handling in the gate, CRLF line endings corrupting materialized workflow
  files, a stale-lock race during concurrent single-use consume, non-atomic
  rename behavior, and a memory-package privacy filter that corrupted
  generated lesson ids before they reached disk.
- The distribution and smoke checks compare the catalogue's legacy mappings to
  the 42 retained command names in `docs/workflow-continuity.md`; the four new
  recipes carry a null `source_command`, as the catalogue contract requires.
- `torque recover --help` shows the public `recover <show|preview|run|discard>`
  grammar instead of the underlying `revert ... exec` names.
- Fix a provenance-check encoding gap by making every read/write in the
  release scripts explicit UTF-8.
- Rename the optional delivery-focus workspace profile to `solution-lead` and
  remove firm-specific prose from tracked source; add a hygiene test that
  fails if any tracked text file names a firm again.

1123 offline tests pass (154 subtests).

## 2.0.0a9 — unpublished continuity and verification update, 2026-09-22

- Recheck session evidence during resumption and handoff. Missing, changed or
  unavailable files stay visible while journal history and reported status remain
  intact; large artifacts are hashed in bounded chunks.
- Diagnose malformed session references and metadata observations with the affected
  record instead of crashing during handoff. Check captured byte counts as well
  as hashes; unavailable captures leave the rest of the handoff usable.
- Inspect all selected-client sessions and changes in `doctor`, and identify the
  running installation. Use clearer consulting-workspace and unrun-check wording.
- Keep private workspace/demo creation outside Torque source even when the CLI is
  installed elsewhere, including extracted source distributions.
- Pin offline tests and their child processes to the current checkout. Pytest
  options with operands no longer silently omit standalone fixture suites;
  `--pytest-only` explicitly selects a smaller development run.
- Update packaged session workflows and verify source and clean installed behavior
  separately. See [current validation](docs/validation.md) for scope and limits.

## 2.0.0a8 — unpublished receipt correctness update, 2026-09-08

- Fetch deployment receipts from the explicitly selected org through Salesforce
  CLI's REST transport, preventing cached deployment targets from misattributing
  another org's result.
- Accept equivalent, checksum-validated 15/18-character deployment IDs while
  preserving the raw values; reject malformed IDs and different jobs.
- Distinguish successful receipt retrieval from a failed deployment outcome, and
  exclude HTTP headers, cookies and unrelated transport output from evidence.
- Complete the prepared live Apex qualification and exact fixture cleanup; add
  14 regression tests and retain current host-access and browser coverage limits
  in [alpha 8 validation](docs/validation-alpha8.md).

## 2.0.0a7 — unpublished practical testing update, 2026-09-07

- Publish complete new clients atomically so interrupted creation can be retried;
  preserve existing clients and concurrent creators' work.
- Include bounded firm and selected-client working notes in client handoffs, with
  accurate Unicode character limits and existing journal/evidence sections.
- Read customized workflow text from the explicitly selected private workspace,
  falling back to the packaged recipe when a local file is absent.
- Generate honest Apex null/empty test drafts with typed overload arguments,
  declaration-scoped methods, explicit unresolved assertions and visible unsupported
  cases. Preserve edited outputs; honor source API versions and shorten long names.
- Report generated drafts as unexecuted QA work, including missing or truncated
  output, instead of declaring passing tests. Remove invented bulk/FLS scaffolds.
- Extend installed workflow checks to verify scoped notes and actual customized
  recipe lookup. See current validation for tested runtimes and live-test limits.

## 2.0.0a6 — unpublished evidence consistency update, 2026-09-07

- Reject a successful deployment summary when supplied component/test details
  report failures or inconsistent completion counts. Preserve the exact job ID
  and partial evidence; summary-only responses remain supported.
- Add current provider data-handling guidance, explicit local/cloud data paths
  and an optional client adoption worksheet. No provider settings are changed
  or new runtime approval gates introduced.
- Incorporate scoped Flow cleanup and string-ID query lessons from the rigorous
  live test cycle. See validation for tested versions and remaining UI coverage.

## 2.0.0a5 — unpublished live-test repairs, 2026-09-07

- Resolve org identity from matching Salesforce Organization data; retain the
  actual IsSandbox value and edition separately from nonproduction behavior.
- Restore only metadata covered by the original selectors, with verified source
  paths, file hashes and complete companion files. Preserve unrelated parent and
  sibling metadata; unverifiable legacy compound captures need manual review.
- Interpret current Bulk API CLI results, retain job identities from failed
  submissions, and distinguish partial application from success in execution and
  polling. Match input CSV line endings without rewriting user data.
- Preserve timeout diagnostics, capture exact-job readback evidence privately,
  and refuse to finalize polling without its evidence bundle.
- Verify the current browser identity before logout, retaining the original
  failed switch; redact encoded Salesforce Setup confirmation tokens.

Bounded live acceptance, fixture cleanup and installed-artifact validation are
recorded in [alpha 5 validation](docs/validation-alpha5.md). Four non-admin browser
cases remain blocked; no publication or universal coverage is implied.

## 2.0.0a4 — unpublished workflow update, 2026-09-07

- Incorporated research into eight operating recipes and the shared tools rule:
  use current official Salesforce guidance and the client's existing delivery,
  test and migration stack; retain native evidence in Torque's engagement records.
- QA now starts with business criteria and intended actors; migration guidance
  distinguishes simulation from target-write acceptance; updates carry packaged
  improvements into private workspaces while preserving local edits.
- Added a concise research-adoption map and an optional tooling-context example.
  This is guided tool composition, not a claim of tested native integrations.
- No new runtime features, dependencies, mandatory records or approval steps.

## 2.0.0a3 — unpublished development build, 2026-09-07

- Added an offline synthetic consulting demo with prepared source, real local
  observations, three unrun acceptance criteria and a native handoff.
- Added optional engagement changes connecting business outcomes, criteria,
  decisions, captured evidence, exact metadata observations and next actions.
- Added client listing, structured CLI output, capability-specific local doctor
  checks and direct deploy/data/org/recover command routes.
- Added workspace workflow upgrades that preserve local edits, track packaged
  baselines, handle interrupted updates and serialize cooperating updates.
- Corrected browser identity, incomplete coverage, cleanup, credential diagnostics
  and artifact-path handling. Named-user behavior requires live qualification.
- Corrected QA incomplete-result exit behavior and browser evidence discovery.
- Corrected bulk-delete field capture and operation lease handling. Bulk recovery
  remains manual; observed scope and limitations are retained in captures.
- Expanded public documentation, product comparison, acceptance protocol and CI.

See [validation](docs/validation.md) for the tested artifact and exact limits. An unpublished local
build and configured CI are not a public release or a passed remote CI run.

## 2.0.0a2 — local continuation baseline, 2026-09-07

Bounded live metadata/data/recovery acceptance in a selected disposable Developer
Edition org; improved create diagnostics, changed-field restoration and permission
assignment result handling. Eight exact metadata jobs succeeded and test artifacts
were removed from active use. This did not establish non-admin browser or Flow UAT.

## 2.0.0a1 — local continuation baseline, 2026-09-07

Continued the reusable JusticeserverClaude framework in Torque, preserving 42
original conversational mappings, adding private employer/client workspaces,
49 recipes, portable review skills, selected generic runtime packages, a session
journal and handoffs. Retired the previous Torque enforcement runtime from the
default product; retained a private checkpoint for historical recovery.
