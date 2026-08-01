# Torque

**Let an AI agent do real Salesforce work across every org you run — production included — with
guardrails that actually hold.**

Frontier coding agents can already query, deploy, run Apex and move data. What stops most people
using them on orgs that matter is not capability — it is that the same session which fixes a flow
can, with one mistaken alias, write to a client's production org, and telling the agent to be
careful does not bind it.

Torque is the layer that binds it, so you can use the capability. Sandbox and developer orgs move
freely. Production moves too — on an approval you issue at your own terminal, in one command. A
torque wrench is not a weaker wrench; it is the one you reach for when the number matters.

---

## See it in 3 seconds — no org, no credentials, no risk

```
git clone <this repo> && cd torque
python3 bin/torque-demo
```

Real attacks, run through the real hooks:

```
Shell indirection
The gate never sees the literal word `sf` — it is assembled at runtime.
  DENIED  x=sf; $x data delete bulk --sobject Account --target-org acme-prod
          variable-assembled command → Salesforce operation hidden in a shell assignment value
  DENIED  s$'\x66' data delete bulk --sobject Account --target-org acme-prod
          ANSI-C hex escape spells 'sf' → indirect command invocation cannot be
          authorized — call `sf` literal

Path expansion
A glob or variable that only becomes the secret's path when bash expands it.
  DENIED  cat /Users/you/.tor*/secret
          glob reaches the signing secret → reference to the trust anchor (~/.torque) —
          secret and tokens are operator-only
  DENIED  p=$HOME/.torque;cat $p/secret
          path assembled through a shell variable → reference to the trust anchor (~/.torque)

Overwriting the gate itself
Disable the hook and everything downstream is ungated.
  DENIED  : >hooks/lib.py >/tmp/z
          two redirects; bash truncates BOTH → write to a protected path: hooks/lib.py
  DENIED  git checkout HEAD~5 -- hooks/
          restore an older, weaker gate from git → git checkout targeting protected paths

Normal work is untouched
A gate that blocks real work gets turned off. These all run.
  allowed sf data query --query "SELECT Id FROM Account" --target-org any-org
  allowed grep -rn 'sf data delete --target-org' .
  allowed sf project deploy start --dry-run --manifest p.xml --target-org acme-prod

  all 24 behaved correctly
```

Every attack there was found by an adversarial audit against an earlier version of this code, and
is now a permanent regression fixture. The last section matters as much as the rest: **a gate that
blocks real work gets switched off.**

---

## How it holds — five layers

1. **Credentials.** Connect production read-only. Torque stores no org credentials; the `sf` CLI is
   the only credential path. (It does create one local signing secret, `~/.torque/secret`,
   which is what makes approval tokens unforgeable.)
2. **Authorization by identity, not inference.** A non-production write is allowed only when its
   target is on an explicit allowlist **and** classifies non-production from a *live* org query at
   the moment of the write — never from an alias or a URL guess.
3. **Deterministic gates.** Two PreToolUse hooks share one expansion-aware parser. They authorize
   by *parsing argv* and default to deny — so indirection, grouping, wrappers, interpreters
   carrying a Salesforce target, glued redirects, legacy `force:data:*` verbs and glob/`$var`
   path tricks are refused rather than guessed. A script that carries no visible target is a
   different matter, and the guide's threat model says so rather than implying otherwise.
   Destructive operations and anonymous Apex need an operator-present, HMAC-signed,
   single-use token. **The agent can request approval; through its tool surface it provably cannot
   mint one.** A crashing gate denies rather than opens.
4. **Production override — deliberate, not impossible.** Real work includes deploying to a client's
   prod. `torque approve <org> <op> --prod` mints a single-use token (you type `WRITE PRODUCTION`
   at a real terminal); `torque approve <org> --session <min>` opens a bounded, revocable window.
   Every production write is audited.
5. **Verified enforcement.** Every rule is labeled hook-enforced, harness-enforced, or
   model-honored — and the labels are *checked*, not decorative.

---

## What it knows, and what it learns

Enforcement is the part that has to be right. It is not the part that makes Torque worth using
day to day — this is.

**The platform answers back, at the moment of the operation.** Torque carries a catalogue of
Salesforce behaviours where every entry declares how it is known: re-checked against a live org,
cited to Salesforce, or plainly marked as learned the hard way. The gates already read every
command, so the relevant entry prints as the command goes past — including on a refusal, which is
exactly when it matters, because that is when you are deciding whether to override.

```
$ sf data delete bulk --sobject Account --file ids.csv --hard-delete --target-org acme-prod

TORQUE GATE DENY [destructive_data_gate] operation targets protected sObject Account
TORQUE PLATFORM NOTE [sandbox-contains-real-data] Full and Partial Copy sandboxes contain real production data
  → Treat "sandbox" as insufficient grounds for a destructive operation. Confirm the sandbox TYPE, and remember that data masking is opt-in and frequently not configured.
TORQUE PLATFORM NOTE [recycle-bin-retention] A deleted record is recoverable for a bounded window, and which delete ran decides whether there is one
  → Prefer a normal delete and let the bin be the undo. Reserve hard delete for the cases that genuinely need it — a duplicate-key constraint, or bin capacity on a high-churn object — and say so out loud before…
```

Ordinary work stays silent. The harness re-runs every `verified-live` claim against a real org
and fails when one stops holding — Salesforce ships three releases a year, so a platform fact
recorded once and never re-checked is decaying from the day it is written.

**`torque blast-radius` — what the operation will actually set off.** "Update Type on every
Prospect account" reads like one operation on N rows. It is N rows, plus every active trigger and
record-triggered flow, plus every validation rule that must now pass on records saved years ago
under different rules, plus every roll-up that recalculates, plus — on a delete — the children
that cascade and the lookup children left pointing at nothing. All of it is queryable. Nothing
assembles it and puts it in front of you.

```
BLAST RADIUS — update on Account @ sf-coffee
  criteria : Type='Customer - Direct'
  scope    : 24 record(s)
  triggers : none
  flows    : 1
             · Coffee 01 - Account Tier (Before Save) [RecordBeforeSave]
  rules    : 1  (must pass on records saved under older rules)
             · Volume_Must_Be_Positive
```

Any source that cannot answer reports **UNDETERMINED** and the exit code turns 3. A blast radius
that silently under-reports is worse than none, because somebody would act on it.

**It gets smarter from being used, without being asked.** `torque lesson` turns something learned
into a catalogue entry the schema enforces or a gate fixture that runs forever — never a free-text
note, because note-based lesson systems reliably go inert. But the right *format* does not fix
capture: nobody types six flags at the moment they learn something, because that moment is always
inside an incident. So Torque watches instead, on one deliberately narrow signal — a Salesforce
operation that failed with a code from the platform's own error taxonomy — and pairs it with the
later command of the same shape that worked.

```
$ torque lesson review

[1] REQUIRED_FIELD_MISSING  (data:create:record|Contact)  2026-08-01
    failed : sf data create record --target-org sf-coffee --sobject Contact --values "Description=torque-probe"
    worked : sf data create record --target-org sf-coffee --sobject Contact --values "LastName=ZZTorqueProbe Description=torque-probe"
    reason : Creating record for Contact... Error Error (1): Required fields are missing: [LastName]
```

It writes nothing anyone will read as knowledge. Candidates land in `local/`, redacted and 0600,
and reach the catalogue only through `torque lesson`, where the schema and the live verifier still
apply. And the queue cannot rot quietly: the harness reports its age, so an unconverted backlog
becomes a visible warning rather than a file nobody opens.

---

## Run it against your own org

Prerequisites: [Claude Code](https://claude.com/claude-code) (the hooks are its PreToolUse surface),
`git`, `python3` ≥ 3.8, the [Salesforce CLI](https://developer.salesforce.com/tools/salesforcecli) ≥ 2.60,
and any non-production org — a [free Developer Edition](https://developer.salesforce.com/signup) is fine.
(`node` is optional; it only affects the live browser check.)

```
sf org login web --alias my-dev-org
python3 bin/torque init my-dev-org        # verifies the org is NOT production, then configures
npm install                               # optional: only the live browser check needs this
python3 harness/validate.py --profile release --target-org my-dev-org
```

Skip `npm install` and everything still runs — the browser check reports `BLOCKED` with a dated
reason and the verdict is `DEGRADED` rather than `PASS`. That is deliberate: a check that cannot
run is never reported as green.

`torque init` refuses to allowlist a production org, creates the trust anchor outside the repo,
and proves the gates bind before telling you it worked. The allowlist is deliberately not shipped:
which org you may write to is a decision only the person at the keyboard can make.

Then open the folder in Claude Code and work. The hooks fire on the tool calls that can reach an org or the gate's own files — Bash, MCP, Edit/Write/MultiEdit and Read.

---

## Why you can believe it

Torque validates itself the way it validates Salesforce work. `--profile release` runs:

- **196 gate fixtures** (193 recorded on disk, 3 HMAC tokens minted during the run) — every
  attack class found across the audits, each one a named,
  runnable test.
- **11 mutation tests** — each temporarily neuters one guard and *requires* the corresponding
  attack to succeed (or, for the static scanners, requires the check to FAIL). A check that
  cannot fail proves nothing; these prove each guard is load-bearing. Seven run on any clone;
  three exercise the clean-IP scan and need the private pattern list, so they report as
  operator-only rather than pretending to pass; the eleventh needs `--target-org`, because the
  check it neuters queries an org. A skipped mutator is never reported as caught.
- **A live capability cycle** against your org — deploy a field, verify it by SOQL, verify
  field-level security, hard-delete it, confirm zero residue; a mass-update with a working undo;
  a real headless Lightning render.
- **Release gates** — an agent-side token mint must fail, and seven bypass shapes must deny.

The safety design was driven to convergence by a nine-round multi-model audit *before* the code
existed, then the built gates by four independent adversarial rounds — a shell-semantics reviewer,
a reviewer that *executed* each exploit against a real `sf` CLI, and an architecture skeptic —
plus two confirmation passes. Roughly 55 real vulnerabilities were found and fixed; each one is a
fixture. The trail is in [`harness/VALIDATION.md`](harness/VALIDATION.md).

---

## The guide

[`guide/Torque-Guide.pdf`](guide/Torque-Guide.pdf) — 14 pages: what it does, why it isn't
the MCP server, setup, the operations worked through, the safety model, troubleshooting,
and how the harness proves itself.

---

## What Torque does *not* claim

The gates bind the agent's **tool surface** (Bash / Edit / Write / Read / MCP). They stop accidents
with certainty and defeat enumerable circumvention. They do **not** claim to stop a same-user actor
who steps outside that surface by executing arbitrary code — forging a login session with a bespoke
program, reading the signing secret through the OS, or having the agent write a script and run it
so `sf` executes as a subprocess no hook ever sees.

For those, the load-bearing defense is layer 1: connect production read-only and never leave a
production org authenticated in an autonomous session. Closing the subprocess channel properly
needs a PATH-level shim that classifies before `exec` — that is the v2 roadmap, and it is not
built yet. Saying so is the point: a safety claim you cannot reproduce is marketing, not security.

**Found a way through?** That's the whole point — every fixture in the suite exists because a
review beat an earlier version of this code. See [`SECURITY.md`](SECURITY.md) for where to send it.

---

## What it isn't

Not a CI/CD pipeline (Gearset, Copado). Not an in-org codegen IDE (Agentforce Vibes). Not a command
library (sfdx-hardis — good prior art; Torque could sit on top of it). It is the operator-grade
safety and validation layer those categories don't ship.

**Full guide, safety model and threat model:** [`guide/Torque-Guide.pdf`](guide/Torque-Guide.pdf)
**Validation log and audit trail:** [`harness/VALIDATION.md`](harness/VALIDATION.md)

MIT licensed.
