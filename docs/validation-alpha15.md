# Validation for alpha 15: September 24, 2026

Alpha 15 is a development build with no package-index release. It adds opt-in
[connected mode](connected-approval.md): a third `ai_access` value under which an AI
session works in one client's approved orgs and makes each org write only by consuming an
approval the consultant granted for that exact command. A workspace that does not opt in
behaves as in alpha 14: every existing gate, workspace and CLI test passes, and the only
edits to existing tests generalize alpha 14's version and changelog checks so they accept a
later version.

## What was built

New modules under `src/torque/`: `presence` (real terminal, no agent environment, no agent
ancestor process, typed code), `consent`, `connected_routes` (quote-aware route
classifier), `namespaces`, `before_state`, `approval`, `gate_connected`, `cli_approval`,
`permissions`, `doctor_connected`, and the rule file `data/connected/production-approval.md`.
Changed: `gate.py` (mode chain, an `org_rules` switch and per-folder client guard for the
path scan, consent and approval files guarded, connected dispatch and `ask` output),
`workspace.py`, `changes.py` (approval and before-state events), `cli.py`, the revert
wrappers and revert executor (re-check of the consumed approval and live org ID), and
`delivery-practice.md` (one line).

The host facts connected mode relies on (hook input keys, the `ask` output, permission rule
syntax, the agent's environment markers, the Salesforce CLI's read commands) were checked
against Claude Code 2.1.281 and Salesforce CLI 2.150.6 and are recorded in
[connected mode](connected-approval.md#host-facts-verified).

## Results

- **Offline suite (macOS, Python 3.14.7, local):** 2572 pytest tests and 154
  subtests pass (2 skipped: one Windows-only test, and the private denylist check, which
  runs only where the owner's private list is configured), and the 12 standalone fixture
  suites complete. That is 397 more tests than alpha 14's 2175. The wheel and source
  distribution checks and the installed-wheel smoke test pass. No live org or provider call.
- **Hook probes:** events piped through the real hook command (`python -I -c ...`) against
  a scratch connected workspace with a synthetic client, bound with `TORQUE_CLIENT`, each
  answered in 0.06 to 0.07 seconds: a read of an approved org, allowed; a read of an org
  outside the consent, denied; an unapproved write, denied; a check-only deploy, allowed and
  logged; `python3 tools/fix.py`, ask (denied in `bypassPermissions`); `torque approval
  grant`, denied; a read of another client's folder, denied; a write to `consent.json`,
  denied; a browser click without a window, denied. After a grant, the approved write was
  allowed once and denied on replay, and the change recorded `before_state`,
  `approval_request`, `approval_grant` and `approval_consume`. `torque doctor` in that
  workspace ran its five probes through the hook and got the expected deny, deny, ask,
  deny and deny.
- **CI:** `Validate Torque` on the pull request, all 9 cells (Ubuntu, macOS and
  Windows, each on Python 3.10, 3.12 and 3.14). The run id is recorded on the pull request.

These are synthetic, offline checks. No Salesforce org, browser or model provider was
called.

## Review scope

Done by the implementer while building: a hostile pass over the gate, route classifier,
approval store and presence check, looking for an unapproved write, a replay, a
cross-client read or a self-grant through a recognized route. It led to these changes, each
with a regression test: the approval binds the working folder (the same command run from
another folder deploys other files); the grant re-derives everything from the request's
command rather than trusting the request file; `sf alias set` and `sf config set` are
refused; the Salesforce CLI's credential, alias and configuration folders, its
installation and writable folders on `PATH` are guarded from the file tools; before-state
captures count as org reads subject to the consent; a custom Apex REST call or a request
file counts as a write; desktop-control tools other than screenshots are refused (they could
type into the consultant's terminal); unknown programs ask rather than pass.

**R1, hostile QA** (run by the owner's controller on df87835) checked ten invariants: four
held (single use, writes need an approval, one client per session, full and build-only
unchanged) and six did not. Each gap now has a regression test in
`tests/test_gate_connected.py`, written failing first, and a fix: the change record and
the consent's org ID are part of the binding at use; `sf org display` without an org flag
is refused like any other read without one; a production browser window needs a recovery
path; the grant screen escapes non-printable characters; a named permission mode other
than `default`, `acceptEdits` or `plan` is refused for unchecked programs (a missing mode
still asks, which the doctor probe and hosts that send no mode rely on); the gate uses an
approval only after the rest of the call is allowed. The connected-mode page now states
exactly these rules.

**R2, independent spec-conformance review** (run by the owner's controller on 1e6c8db)
reported 18 defects (2 critical, 14 important, 2 minor) and rated 36 of 74 requirement rows
partly met or not met. Its tests are in `tests/test_connected_r2.py`, written failing
first (50 of 52 failed). The fixes: the file binding follows every payload flag value,
legacy spellings, tree-import plans and MCP file inputs, and refuses missing files and
links; a browser window is bound to the org the browser tools last navigated to; record and
log reads need their class in every recognized form; consent needs a dated sign-off by the
right client at every use; client listing and out-of-consent orgs on any route are refused;
skipped prompts refuse writes and browser changes; `sh -c` asks; before-state coverage is
content-level, covers source folders and checks the capture's org and order; wrappers find
connected mode themselves, fail closed and verify the approval again; a wrapper that cannot
resolve the org returns the approval; audit records are mandatory; large payloads use the
wrapper route; the grant reads the check-only result and compares an attached audit trail;
typed codes on every owner step; tier 2 is enforced as a separate account and refused on
Windows; the permission rules and doctor checks are wider. Where the report was not
followed in full, the private report records why.

**R2 recheck** (on 3e89afb) reported 10 findings not fully fixed and three new ones. Its
tests are in `tests/test_connected_r3.py`, written failing first (27 of 27 failed). The
fixes: default deploy scope, shared metadata files and attached multi-value flags in the
file binding, with components that have no local file refused; browser changes bound to the
exact tab and the org it alone was sent to; decoded REST paths; destructive manifests and
definition files in before-state coverage; wrappers that refuse an indeterminate workspace
or another working folder and claim atomically; a revert child's failure returning the
parent approval; enforced approval-event schemas; exact permission overlap; record exports
normalized into record evidence; a path-resolving full-mode guard; an unbound check-only
doctor probe; MCP writes without files; and consent checked for the right client. The
browser binding still rests on the navigation the session asked for, not its result; that
limit is stated in [connected mode](connected-approval.md#what-it-cannot-stop).

**Round 4** (the recheck of round 3, with the owner's ruling on browsers): browser MCP and
devtools changes are refused in connected mode, and Torque's own browser checks the org ID
at start and each request's actual origin while it runs; bundles are bound whole; recovery
coverage needs every file the recovery restores; recovery approvals bind their snapshot;
destructive deploys bind their manifests and check deletions against the before-state; the
full-mode guard follows directory changes. Tests: `tests/test_connected_r4.py`, written
first (17 of 22 failed before the fix; the other 5 are controls that must keep passing; a 23rd checks that the browser session installs the guard).

Two exceptions to "unchanged" are deliberate and documented in [build-only
mode](ai-access.md): consent, approval and key files are refused wherever the hook runs,
`full` mode included, and three alpha 14 record tests were generalized for the new version.

Pending, and run by the owner's controller rather than in this build:
- **Re-review of the R2 fixes** by the reviewers, against the "What it stops" list in
  [connected mode](connected-approval.md).
- **Live rehearsal** in a disposable Developer Edition org with synthetic data under a
  synthetic client: launch; read; unapproved write denied; request with a captured
  before-state; grant from a separate terminal (tier 2 if a second OS account is available);
  write allowed once; replay, changed payload and out-of-consent org denied; script asks;
  browser click denied, then allowed inside a window; `torque recover` preview and run;
  `approval log`. It needs a developer org the owner supplies. Doctor probes inside a real
  Claude Code session are part of it.
- Owner sign-off, then the `v2.0.0a15` tag.

## Known remaining limits

See [what connected mode cannot stop](connected-approval.md#what-it-cannot-stop). In
short: code the session writes and runs, tier 1 key forgery, actions inside a browser
window, alias changes before a raw `sf` write between doctor runs, parallel tool calls,
and a hook that does not run. Tier 2's operating-system setup (a second account with read
access to the workspace) is documented for macOS ACLs and has not been rehearsed.
