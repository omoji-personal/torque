# Torque — running an AI agent on live Salesforce orgs, safely

*Draft — phase status at the end. This guide grows with the build; every claim below is
backed by a harness check you can run.*

## The problem

Frontier coding agents can now operate Salesforce orgs directly — read, deploy, run Apex,
move data — through the CLI and the official MCP server. That is enormously useful and
genuinely dangerous: the same session that fixes a flow can, with one mistaken alias,
write to a client's production org or delete live data. Prompt instructions ("be careful,
don't touch prod") do not bind an agent under load. **Torque is the discipline that does.**

## The one-sentence claim

**The agent cannot write to production on its own.** Production writes are denied by default
and require a deliberate, operator-present override — never an alias, a config change, or an
obfuscated command. Not "asks first" — *structurally gated.*

## How it holds — five layers

1. **Credentials (the real boundary).** Connect production read-only. Torque stores no
   secrets; sf CLI auth is the only credential path.
2. **Authorization by identity, not inference.** A non-production write is allowed only when
   its target is on an explicit allowlist AND classifies non-production *at the moment of the
   write* — a live `Organization` query (strict boolean, timeout-safe), not an alias or a URL
   guess. A production or unverifiable org can never be allowlisted into eligibility.
3. **Deterministic gates.** Two hooks share one expansion-aware parser that fires before every
   tool call. They authorize by *parsing* argv and defaulting to deny — never raw-text — so
   parameter indirection (`$x`, `s$'\x66'`), subshell/`{ }`/process-substitution groups,
   wrappers (`nice`, `sudo`, `caffeinate`), interpreters and here-strings (`bash -c`, `eval`,
   `<<<`), `xargs` stdin commands, legacy `force:data:*` verbs, and decoy targets are all
   refused rather than guessed. Destructive operations and anonymous Apex require an operator-
   present token. **The agent can request approval — through its tool surface it provably
   cannot mint one:** tokens are HMAC-signed with a secret the agent's Bash, Edit *and* Read
   tools are all denied, the issuer refuses unless run from a real login session clear of the
   agent, and a token authorizes exactly one operation then is gone. The gate files themselves
   cannot be overwritten (including via `cd`-desync), and a crashing gate denies, not opens.
4. **Production override — deliberate, not impossible.** Real consultancy work includes
   deploying to a client's prod. So production isn't walled off forever; it is *gated on a
   present operator*. `torque approve <org> <op> --prod` (type `WRITE PRODUCTION`) mints a
   single-use token for one operation; `torque approve <org> --session <min>` opens a bounded,
   revocable window. Every production write is audited as a `PROD-WRITE` event. The agent can
   ask for the override; only the operator can grant it.
5. **Verified enforcement.** Every rule is labeled hook-enforced, harness-enforced, or
   model-honored — and the labels are *checked*, not decorative. Nothing claims more than
   it proves.

## Threat model — what the gates bind, and where the boundary honestly sits

Overclaiming is the fastest way to lose a security reviewer's trust, so here is the exact
line. The gates are proven — by 48 adversarial fixtures, and three independent audit lenses
(a shell-semantics reviewer, an execute-the-attack reviewer that ran each exploit against a
real `sf` CLI, and an architecture skeptic) — to stop **both** categories that matter:

- **Accidents**, with certainty: a wrong alias, a forgotten `--target-org` (which would
  silently use the default org), a fat-fingered bulk delete. This is the dominant real-world
  risk and it is closed structurally.
- **Enumerable circumvention** by a capable agent through its tool surface: parameter and
  ANSI-C indirection, subshell/brace/process-substitution grouping, wrapper runners, `eval`
  and here-strings, `xargs` stdin commands, quote tricks, legacy CLI verbs, decoy targets,
  redirects or a `cd`-desync aimed at the gate's own files, forged or unsigned tokens, and
  destructive operations routed through MCP under a non-obvious tool name. Each has a named,
  runnable fixture that must deny; the parser fails closed on any indirection it cannot resolve.

What the gates **do not** claim to stop — and no PreToolUse hook honestly can — is a
same-user actor who steps *outside* the tool surface by executing arbitrary code:

- forging a login session (e.g. a bespoke pty-reparenting program) to fake operator presence,
  or reading `~/.torque/secret` by OS means the agent's gated tools never touch — both are the
  filesystem/credentials trust boundary (Layer 0), not something a hook adjudicates; and
- having the agent write a script and run it, so that `sf` executes as a subprocess the hook
  never sees.

For those, the load-bearing defense is not the gate — it is **layer 1**: connect production
read-only, and never leave a production org authenticated in an autonomous session. Even a
write that fully bypasses the gate then meets an org that fails the live non-production check
with no operator override present. The v2 roadmap closes the subprocess channel with a
PATH-shim (`sf` resolves to a classifier that runs before exec); v1 raises the direct-tool-
surface bar as high as a parsing gate honestly can, and states the rest plainly.

## Why you can believe it

Because Torque validates itself the way it validates Salesforce work: a harness that
exercises every capability against a real Developer Edition org, with mutators proving each
safety check can actually fail. A representative run deploys a field, confirms it by SOQL,
verifies field-level security, hard-deletes it, and confirms zero residue — live. The run
log is `harness/VALIDATION.md`; reproduce it on any free DE org with
`harness/validate.py --profile capability --target-org <your-org>`.

The plan behind Torque was itself driven to convergence by a nine-round multi-model
adversarial audit — the same "prove it, don't assert it" discipline, applied to the design
before a line was written.

## What Torque is *not*

- **Not a CI/CD pipeline** — that is Gearset / Copado's category.
- **Not an in-org codegen IDE** — that is Agentforce Vibes.
- **Not a command library** — sfdx-hardis is that, and it is good; Torque could sit on top.

Torque is the operator-grade safety-and-validation layer none of those ship: the discipline
for letting one agent *operate*, across any org, any firm.

## Running AI safely in orgs you don't own

For a consultancy deploying AI into client environments, the liability is not the model —
it is blast radius. The questions a client's security team will ask are: *what can it write,
where, and how do you prove the guardrails bind?* Torque answers with an allowlist a client
can inspect, a boundary where the agent cannot write to production on its own — a deliberate,
logged, operator-present override is the only path — and an audit trail rendered from
machine-readable attestations. "Trust us" becomes "run the harness."

---

**Phase status (2026-07-31):** P0 (validation loop), P1 (safety core — gates, approval,
allowlist), P2 (deploy knowledge + live probe cycle) COMPLETE and green on a live org.
P3 (skills, agents, installer), P4 (browser verification), P5 (release + full guide) in
progress. **No release entry exists yet** — this is the M1 draft.
