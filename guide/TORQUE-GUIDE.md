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

**Torque exposes no configuration path that authorizes a write to a production-classified
org.** Not "asks first" — *ineligible by construction.*

## How it holds — four layers

1. **Credentials (the real boundary).** Connect production read-only. Torque stores no
   secrets; sf CLI auth is the only credential path.
2. **Authorization by identity, not inference.** A write is allowed only when its target
   is on an explicit allowlist AND classifies non-production *at the moment of the write*
   — a live `Organization` query, not an alias or a URL guess. Production and unverifiable
   orgs cannot be allowlisted at all.
3. **Deterministic gates.** Two hooks fire before every tool call. Writes must name their
   org explicitly; destructive operations and anonymous Apex require an operator-present
   approval token. **The agent can request approval — it provably cannot mint one:** the
   issuer refuses unless run from a real terminal whose process ancestry is clear of the
   agent. A planted token authorizes exactly one operation, then is gone.
4. **Verified enforcement.** Every rule is labeled hook-enforced, harness-enforced, or
   model-honored — and the labels are *checked*, not decorative. Nothing claims more than
   it proves.

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
can inspect, a boundary that makes production writes structurally impossible, and an audit
trail rendered from machine-readable attestations. "Trust us" becomes "run the harness."

---

**Phase status (2026-07-31):** P0 (validation loop), P1 (safety core — gates, approval,
allowlist), P2 (deploy knowledge + live probe cycle) COMPLETE and green on a live org.
P3 (skills, agents, installer), P4 (browser verification), P5 (release + full guide) in
progress. **No release entry exists yet** — this is the M1 draft.
