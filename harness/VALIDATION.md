# Torque — validation log

Rendered from harness runs. Each entry records what was exercised, against which org, at
which commit. Third-party-reproducible checks are unmarked; operator-only checks (those
needing the private clean-IP denylist) are labeled.

Bootstrap: any free Salesforce Developer Edition org works as the target for the
non-org-bound checks and the (future) probe cycle. `harness/validate.py --profile static`
needs no org at all.

---

## P0 — the loop proves itself · 2026-07-31

**Commit:** `dbe9122` · **Target:** sf-coffee (personal Developer Edition) · **Verdict: PASS**

| Profile | Checks | Result |
|---|---|---|
| static | byte_budget, local_ignored, tooling_ignore_exact, clean_ip*, secret_scan | PASS |
| capability | + org_classify, describe_first | PASS |

**Self-test (mutators — each catastrophe-class check proven able to FAIL):**
clean_ip (tracked term) · secret_scan (token shape) · clean_ip historical-blob (term in
history, removed from HEAD) · clean_ip fail-closed (denylist absent) — **all 4 caught.**

**Highlights**
- `org_classify` read `sf-coffee` live: IsSandbox=False, Developer Edition → verdict
  `developer` (three-valued; developer is not production).
- `describe_first` proved a real field (Account.Name) resolves and a hallucinated one
  (Account.Torque_Not_A_Real_Field__c) is refused — against the live org.
- `clean_ip`* scans five surfaces incl. historical blob CONTENTS (bytes-safe) and is
  fail-closed on an absent/short denylist. *operator-reproducible (private denylist).

**Phase status:** P0 complete. P1 (safety core: gates, TTY-bound approval, allowlist) not
yet built. No release entry exists yet.

---

## P1 — safety core binds · 2026-07-31

**Target:** sf-coffee (developer, disposable, allowlisted) · **Verdict: PASS** (16 checks)

Two deterministic hooks registered on Bash, MCP-write, and Edit/Write matchers:

- **prod_write_gate** — authorizes writes by identity: explicit `--target-org` required
  (no-target, compound `config set target-org && write`, and inline `SF_TARGET_ORG=`
  shapes all denied), org must be on the allowlist AND classify non-production live.
  Agent Edit of the allowlist / protected-objects is denied.
- **destructive_data_gate** — bulk/hard delete, WHERE-less update, destructive metadata,
  and anonymous Apex require an operator-present token; `--file` required for Apex
  (stdin denied); protected sObjects (Account, Contact, Opportunity — sf-coffee's demo
  data) shielded on every org.

**The approval boundary, proven live:** `torque approve` refused from the agent shell
(no TTY) and from a `script`-wrapped pty; a planted operator token authorized a bulk
delete exactly once and was denied on reuse (single-use). *An agent can request approval;
it cannot mint one.*

Checks added: gate_write_authz (5 cases), gate_destructive (4), approval_boundary,
preflight_credentials (54 orgs enumerated), local_hygiene (0600 enforced), enforcement_map
(hook-enforced labels resolve to registered hooks).

**Phase status:** P0, P1 complete. P2 (probe cycle → M1) next.

---

## P2 / M1 — deploy knowledge + live probe cycle · 2026-07-31

**Target:** sf-coffee · **Verdict: PASS** (17 checks) · **MILESTONE M1 (sendable)**

`probe_cycle` performed a REAL round trip against the live org: deployed a run-scoped
`Torque_Probe_<epoch>__c` field + a PermissionSet granting FLS → dry-run gated → deployed →
SOQL-verified the field exists → verified FieldPermissions (FLS) → hard-deleted via
`purgeOnDelete` (no 15-day-queue accumulation) → confirmed **residue=0**. Knowledge rules
added: live-verification (harness-enforced by describe_first), deployment (harness-enforced
by probe_cycle), platform-quirks (model-honored, honestly labeled) + a load-on-demand
detail file.

Two self-catches during the build, both the tool's own discipline working: secret_scan
flagged pattern-shaped literals in a hygiene check and in `lib.py`'s redactor — fixed by
assembling those patterns from split strings so the files stay scannable without
self-matching (the plan's U4 principle, met in practice).

**M1 basis:** all-PASS capability run (incl. the excepted-org and credential-posture
checks), guide DRAFT present (`guide/TORQUE-GUIDE.md`), no images, phase-status table
stating no release entry exists yet. **M1 is sendable.**

---

## P3 — skills, agents, installer · 2026-07-31

**Target:** sf-coffee · **Verdict: PASS** (21 checks)

Five skills (orgs, audit-org, safe-deploy, mass-update, session-log), each justifying what
it adds over the raw CLI. Two subagents: org-explorer (read-only `tools:` allowlist) and
hostile-qa (no write tools). Optional user-level gate installer records TORQUE_HOME so gate
inputs resolve independent of CWD.

`mass_update_cycle` ran a REAL cycle on the live org: created two flagged test Accounts →
previewed the exact ID set → bounded per-Id update → verified persistence → undo restored
before-values → teardown BY ID ONLY. Residue verified zero. Checks added: skills_justified,
agents_readonly, mass_update_cycle, installer_roundtrip.

**Phase status:** P0–P3 complete. P4 (browser verification) next.

---

## P4 — browser verification · 2026-07-31

**Target:** sf-coffee · **Verdict: PASS** (23 checks)

`frontdoor_noecho` proves the auth handoff writes the session URL to a mode-0600 file and
**prints only the path — the token never reaches stdout**. `browser_render` launched a real
headless Chromium, followed the frontdoor→Lightning redirect, and asserted the
`one-app-nav-bar` shell rendered live (title "Home | Salesforce") — a genuine render, not a
binary-presence guess. The settle-poll pattern encodes the "never networkidle on Lightning"
rule. When no browser binary is present the check BLOCKs with a dated reason (honest
non-green), never faked. `capture.py` is the sole guide-image writer (crop, EXIF-strip,
content-bound manifest); browser-testing.md carries the frontdoor rule and the 2026-07-01
MFA-enforcement fact.

**Phase status:** P0–P4 complete and green live. P5 (release + full guide) next.

---

## Security hardening — round 10 panel + production override · 2026-07-31

**Commit:** `d3ceb27` · **Target:** sf-coffee · **Verdict: PASS** (23 checks, 6 mutators, 48 fixtures)

After P4, the built gates were re-audited by three independent lenses — a shell-semantics
reviewer, an execute-the-attack reviewer (each exploit run against real `sf` 2.144.6), and an
architecture skeptic. They found **17 P0/P1**: the first cut still trusted raw-text regex on
the destructive side, and shell indirection (`$x`, `s$'\x66'`), grouping (`( )`, `{ }`, `<( )`),
wrapper runners (`nice`, `sudo`, `caffeinate`), `eval`/here-strings, `xargs` stdin, a
`cd`-desync that could overwrite the gate itself, package-lifecycle-as-read, and MCP
name-evasion all defeated argv0 matching.

**Fix:** both hooks were consolidated onto ONE shared, expansion-aware classifier
(`hooks/shellparse.py`) that fails closed on any indirection reaching `sf`. `_sf` gained a
fail-safe timeout; `IsSandbox` is now a strict boolean check; the Read tool is gated on the
anchor. Every fixed class has a named fixture (**48 total**) and the two gate guards each have
a self-test mutator proving they can fail.

**Production override** (operator decision — real consultancy work includes deploying to a
client's prod): production is denied by default; `torque approve <org> <op> --prod` mints a
single-use HMAC token and `torque approve <org> --session <min≤120>` opens a signed, revocable
window, both issued only from a real login TTY clear of the agent. Precedence when production:
session grant → single-use token → deny. Every prod write is audited `PROD-WRITE`.
Override precedence unit-tested 6/6 (deny-by-default, session allow, expired deny, forged-sig
deny, token allow+consume, single-use).

Round 11 re-audits the shared parser and the override.

---

## Security hardening — rounds 11–13 (adversarial convergence) · 2026-07-31

**Commits:** `d4827b9` → `c97f7c2` · **Target:** sf-coffee · **Verdict: PASS** (23 checks, 10 mutators, 109 fixtures)

The built gates were driven through four full adversarial rounds — three independent lenses
each (a shell-semantics reviewer, an execute-the-attack reviewer running exploits against a
real `sf` 2.144.6, and an architecture skeptic) plus a standing self red-team. Each round the
score of the *findings* fell in severity, which is the convergence signal:

| Round | Character of findings | Outcome |
|---|---|---|
| 10 | Architectural — regex is not a security boundary | shared parse-argv classifier |
| 11 | Refinement — legacy `force:data:*` verbs, MCP name-evasion, indirection, decoy targets | 74 fixtures |
| 12 | New front — the gates judged PRE-EXPANSION text (`cat ~/.torq*/secret` read the secret); + alias TOCTOU, PATH-injected `who`/`ps`, one-token-two-deletes; + 6 usability regressions | expansion-aware path guards |
| 13 | Completion of the round-12 front — `**` recursive-glob + char-class basenames misaligned the component matcher | a proper DP glob-prefix matcher |

Every fixed class carries a named fixture (**117 total** across base + r11 + r12 + r13) and each
catastrophe-class guard carries a **self-test mutator** that must flip a deny→allow when the
guard is neutered (**10 mutators**, all caught): clean-IP ×4, anchor-guard, destructive-token,
redirect-detection, wrapper (`wrapped_sf`), expansion-awareness, glob-matcher (`_glob_reaches`).

A final scoped confirmation pass then found 5 more refinements of the same fronts — a `$HOME`
that wildcarded to the wrong anchor, a redirect glued to a preceding word, raw `sf api request`
DML, camelCase MCP tool names, and an `ln -s ~/.sfdx` symlink-then-read — each fixed with a
fixture. The convergence signal was severity, not volume: every later round's findings were
refinements of known fronts (expansion, redirect, MCP naming), never a new architectural class.

**What the rounds established as SOUND (confirmed holding across rounds):** the HMAC token core
(atomic single-use claim, forged-signature rejected), `torque-approve`'s login-TTY + full-
ancestry refusal of the agent, non-cache-poisonability, allowlist protection, and — after the
expansion fix — the anchor/auth-store being unreachable through the agent's Bash, Edit/Write,
and Read tools. The residual is the explicitly disclaimed **Layer 0**: a same-uid actor who
forges a login session with a bespoke program, discloses the secret by OS means (/proc,
ptrace), or spawns `sf` from a script it writes and runs. Those are credentials/OS trust, not
adjudicable by a PreToolUse hook, and the guide states them plainly.
