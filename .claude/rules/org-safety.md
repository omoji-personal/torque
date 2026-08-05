# Org safety

**Through its tool surface, the agent can never write to production on its own.** A write to a
production-classified org is denied by default; production writes happen ONLY through a
deliberate, operator-present override (Layer 3). No alias, no configuration and no obfuscation
authorizes one.

**The qualifier is load-bearing and belongs here, not 80 lines down.** Demonstrated live
2026-08-05: an agent wrote `sf` into a file, ran `bash script.sh`, and deployed metadata to an org
that was NOT on the allowlist — no hook saw it, no audit line was written. The hooks read the
command; they do not open a script the command names. **Without the exec-time shim (Layer 2b) the
allowlist is advisory, not enforced.** Install it, or state the smaller claim.

## Layer 0 — credentials
Connect production read-only / least-privilege. If a firm cannot provide that, the org is not
connected, or connected under a documented client-acknowledged exception in
`local/clients/<client>/<orgid>/`. sf CLI auth is the only credential path; Torque stores no
secrets. `local/` holds org Ids, notes, before-values only.

## Layer 1 — authorization by identity
A NON-production write is allowed only when the target org is on `local/writable-orgs.json`
AND classifies non-production **at write time** — a live `Organization` query (strict boolean
`IsSandbox is True`), never an alias or URL guess, with a callout timeout that fails safe. A
failed check returns `unverifiable`, which denies like production and is **not reported as**
production: an org whose identity could not be established is an unknown, and telling an operator
to approve a production override for one is how that ritual becomes a reflex. Classification costs
two live callouts per gate and both gates classify, so a gated write measures 6.7–13.1s.
Only `sandbox` / `developer` / `scratch` verdicts are eligible; `scratch` needs local dev-hub
evidence, else a trial-shaped org classifies `production`.

## Layer 2 — the gates (deterministic, fail-closed)

ENFORCEMENT: hook-enforced (prod_write_gate)
ENFORCEMENT: hook-enforced (destructive_data_gate)

Both hooks share ONE expansion-aware argv classifier (`hooks/shellparse.py`). They decide "is
this sf, and which subcommand" from parsed argv — never raw-text regex — and **fail closed on
any indirection that could reach `sf` but cannot be statically resolved**: parameter/ANSI-C
expansion (`$x`, `s$'\x66'`), command/process/subshell grouping (`$(…)`, `` `…` ``, `<(…)`,
`( )`, `{ }`), wrappers and unknown runners (`nice`, `sudo`, `caffeinate` …), interpreters and
here-strings (`bash -c`, `eval`, `<<<`), and `xargs`/`parallel` stdin commands. Every mutation
names its target explicitly; there is no default-org path. Destructive operations (bulk/hard
delete, WHERE-not-record-id delete/update, destructive-metadata deploy, anonymous Apex) require
an operator-present, single-use, HMAC-signed token — **you request approval, you never mint
it.** Anonymous Apex must run from the operator-approved immutable copy
`~/.torque/approved/<digest>.apex`. Protected sObjects are shielded over the decoded token
stream on every org. The trust anchor (`~/.torque`: signing secret, tokens, approved copies)
is out of the repo and unreachable through the agent's Bash, Edit/Write, and Read tools; the
gate files themselves cannot be overwritten (including via `cd`-desync). MCP tools all route to
the gates and default-deny — an unknown org-touching tool is treated as a write.

## Layer 3 — production override (operator-present, deliberate, audited)
Production writes require an override only a present operator can create (via `bin/torque-approve`,
which refuses unless run from a real **login** TTY — an active `who` session — clear of the
agent process):

- **Single operation:** `torque approve <prod-org> <op> --prod` — prints the org's instance URL
  + username, requires typing `WRITE PRODUCTION`, mints a single-use, 5-minute, HMAC-signed
  token scoped to that org (+ the destructive-class token, and an approved apex copy, when the
  op needs them).
- **Bounded session:** `torque approve <prod-org> --session <minutes>` (≤120) — a signed,
  time-boxed grant for repeated writes to ONE org; revoke with `--end-session`.

Precedence when the target classifies production: valid session grant → valid single-use prod
token → **deny** (the deny message names both commands). Every production write is audited as a
first-class `PROD-WRITE` event. A dry-run / validate-only deploy is a read and needs no override.

**A shape refusal is not a policy refusal.** When the classifier cannot statically resolve a
command (`$(…)`, an interpreter, an unbalanced quote) it has judged nothing about the operation —
it could not read it. Measured on six months of real commands, 97% of all denials were this, not
policy. With the shim verified those DEFER to it (audited `DEFER`, decided on resolved argv);
without it they still deny, and the message says so and names `install-gates --shim`.

## Layer 2c — observe-only (operator-present, measures instead of blocking)
`torque approve --observe <minutes>` (≤120) makes denials RECORD instead of enforce, so the cost
of the gates can be measured on real work before they block real work. Signed, tree-bound,
anchor-only, revocable with `--end-observe`. **While it is open the org protections are advisory:
a production write that would have been denied proceeds.** It never applies to trust-boundary
refusals (anchor, auth store, protected source, `local/` into git), and an observation that
cannot be recorded denies instead of passing.

## Layer 2b — the exec-time shim (REQUIRED for the allowlist to hold)
Called "optional" here until 2026-08-05, when a script bypass was demonstrated live. It is opt-in
to *install* and load-bearing once you rely on the allowlist. It was also broken until that date —
the re-entry guard sat ahead of the read check, so the gate's own classifying read was refused,
classification always failed, and every write on the machine was denied as production.
`torque install-gates --shim` puts an `sf` shim on PATH, installed into the trust anchor where
the agent cannot read or rewrite it. It decides at exec time, on the argv the kernel is about to
run — bash has finished every expansion by then, so there is no text left to reason about. It
routes writes to the SAME two gates, over an argv payload rather than a reconstructed command
string, and passes reads straight through (which is also what bounds the recursion: a gate
classifying an org runs `sf org display`, and that comes back as a read).

An operator at a real login terminal passes through ungated, checked before any classification,
so a classifier bug can never take an operator's `sf` away from them.

## Boundaries (stated honestly)
The gates bind the agent's **tool surface** (Bash / Edit / Write / Read / MCP) and defeat both
accidents and enumerable circumvention (40+ adversarial fixtures, three independent audit
lenses). Without the shim they do **not** see `sf` spawned as a subprocess of a script the agent
writes and runs; with it installed, that channel is closed for anything that resolves `sf`
through PATH.

What remains open, at any layer: a same-uid actor who invokes the real binary by absolute path,
edits their own PATH, or executes a bespoke program that forges a login session. That is Layer-0
(OS / arbitrary-code) territory and no PreToolUse hook or PATH entry can reach it. The
load-bearing defense there is Layer 1 — connect production read-only and never leave a
production org authenticated in an autonomous session.

## Workspace scope
Hooks load from the active workspace. A session started elsewhere loads no gates — an optional
user-level installer (P3) registers them from a recorded TORQUE_HOME for cross-workspace
protection.

## Test records
Create flagged; delete by Id only — never by name, date, or creator match.
