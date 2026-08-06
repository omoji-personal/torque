# Torque — validation log

Rendered from harness runs. Each entry records what was exercised, against which org, at
which commit. Third-party-reproducible checks are unmarked; operator-only checks (those
needing the private clean-IP denylist) are labeled.

Bootstrap: any free Salesforce Developer Edition org works as the target for the
non-org-bound checks and the (future) probe cycle. `harness/validate.py --profile static`
needs no org at all.

---

## P0 — the loop proves itself · 2026-07-31

**Commit:** `1ca3e22` · **Target:** sf-coffee (personal Developer Edition) · **Verdict: PASS**

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

**Commit:** `ad3e931` · **Target:** sf-coffee · **Verdict: PASS** (23 checks, 6 mutators, 48 fixtures)

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

**Commits:** `953c1db` → `47d6d32` · **Target:** sf-coffee · **Verdict: PASS** (23 checks, 10 mutators, 109 fixtures)

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

Every fixed class carries a named fixture (**172 total** — 114 across base + r11 + r12 + r13, 21 in r14, 23 in r15, 11
in the confirmation set, and 3 valid-token allow-path cases the runner constructs) and each
catastrophe-class guard carries a **self-test mutator** that must flip a deny→allow when the
guard is neutered (**11 mutators**, all caught with `--target-org`): clean-IP ×3 (operator-only — the pattern
list is private), redaction (needs an org), secret-scan, anchor-guard, destructive-token, redirect-detection, wrapper
(`wrapped_sf`), expansion-awareness, glob-matcher (`_glob_reaches`).

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

Two scoped confirmation gates after the four rounds each found deeper variants of the *same*
fronts — an inline var holding an absolute path (`d=.tor;p=$HOME/$d…;cat $p/secret`), multiple
glued redirects, camelCase/acronym MCP names — which were closed with GENERAL solutions (a
command-local var map, `finditer` over every redirect target, server-namespace-scoped MCP write
classification), not one-off patches.

---

## P5 — release · 2026-07-31  *(superseded — see P6)*

**Attestation:** commit `486e638`, tree `0a88062`, sf 2.144.6, node v24.11.1, denylist digest
`129a9dfe…` (attestation file for this run was never written — the generator of the day recorded no check outcomes; see P6) · **profile: release · verdict: PASS**

`python3 harness/validate.py --profile release --target-org sf-coffee` is green end to end:

- **self-test** — 11 catastrophe-class mutators, all caught: each flips a deny→allow (or a
  static check FAIL→PASS) *only* when its guard is neutered, then restores source. Guards proven
  load-bearing: clean-IP ×3, secret-scan, anchor, destructive-token, redirect-detection, wrapper, expansion,
  glob-matcher.
- **24 capability checks** — incl. the live probe cycle (deploy → SOQL+FLS verify → purge,
  residue 0), mass-update → undo, and a real headless Lightning render.
- **4 release-gated checks** — excepted-org hard-fail (no client-prod exception, so the published
  claim stays absolute), a bypass suite over 7 shapes drawn from every audit front, image
  manifest, deliverable coverage (67 tracked paths, all classified).
- **196 adversarial fixtures** across base + r11 + r12 + r13 + r14 + confirmation.

Reproduce on any free DE org with the command above. **Torque is converged and shippable.**

---

## P6 — release · post-audit · 2026-08-01

**Attestation:** `harness/attest/attest-fb89de8d.json` — commit `fb89de8d`, tree `1ad57067`,
working tree clean: true · **profile: release · verdict:
PASS**

Target org `sf-cb-test` (id sha256/16 `262d2e15e8e6f90c`), classified:
sf-cb-test classified 'developer' (IsSandbox=False, Developer Edition)

- **56/56 checks pass**, 0 fail,
  0 warn, 0 skip.
- **11 mutators, all caught** — each neuters one guard and
  requires the attack it blocks to start working.
- Toolchain: sf 2.144.6, node v24.11.1,
  python 3.14.3.

**What this run is evidence of, and what it is not.** It follows a three-model adversarial audit
that found 2 P0 and 11 P1 findings against the previous state — including a fail-open introduced
by a feature added the same week, an `init` path that granted itself write authority, a
credential shape that reached the audit log unredacted, and five checks that could not fail.
All are closed and each carries a check that fails when it regresses.

It is **one run, on one disposable Developer Edition org, by the author**. It is not
independent verification, and the attestation now records enough — commit, tree, org digest,
every check outcome, every mutator — for a reader to reproduce it and disagree.

The previous entry claimed "converged and shipped" and cited an attestation file that was never
written, because the generator of the day recorded tool versions and hashes while its own
docstring promised check outcomes and an org verdict. That is corrected above, and the generator
now refuses to write an attestation containing no outcomes.

---

## P7 — release · after four adversarial rounds · 2026-08-01

**Attestation:** `harness/attest/attest-0c9b805b.json` — commit `0c9b805b`, tree
`c2f6d98f`, working tree clean · **profile: release · verdict: PASS**

Target `sf-cb-test` (id sha256/16 `262d2e15e8e6f90c`): sf-cb-test classified 'developer' (IsSandbox=False, Developer Edition)

**59/59 checks pass. 11
mutators, all caught.** 196 adversarial fixtures, 128 differential-fuzz cases.

### What four rounds found

| Round | Findings | Character |
|---|---|---|
| 1 | 2 P0, 11 P1, 2 P2 | a fail-open introduced by that week's feature; `init` self-authorizing |
| 2 | 16 | mostly defects in round 1's fixes |
| 3 | 10 | every one in code written that day |
| 4 | 1 P0, 5 doc errors | first round on a FROZEN tree — found a pre-existing P0 |

The round-4 P0 is the one worth reading the code for. On a case-insensitive filesystem
`HOOKS/LIB.PY` and `hooks/lib.py` are the same file; the path guard compared case-sensitively, so
the agent could overwrite the gate's own source by shifting the case of a directory. It was the
third case-sensitivity defect, in guards that are not copies of one another — so what they shared
was an assumption, not code, and no duplication check could have seen it.

That changed the method. Code cannot be enumerated; assumptions can. Every path guard is now swept
against ten ways a path can name the same file, which immediately found a fourth instance in all
three guards at once.

### What this run is evidence of, and what it is not

One run, one disposable Developer Edition org, by the author. **Not** independent verification.
The attestation records commit, tree, working-tree cleanliness, the org's identity as a digest,
every check outcome and every mutator — enough for a reader to reproduce it and disagree.

Two earlier entries are worth reading as a pair with this one: P5 claimed "converged and shipped"
while citing an attestation file that had never been written, because the generator of the day
recorded tool versions while its own docstring promised check outcomes. P6 corrected that. This
entry exists because a fourth round found a P0 after P6 was written.

---

## P8 — release · the run this log failed to record · 2026-08-02

**Attestation:** `harness/attest/attest-3c662cf5.json` — commit `3c662cf5`, tree
`a9640819`, **working tree NOT clean** · **profile: release · verdict: PASS** · 210s

Target `sf-coffee` (id sha256/16 `43030f28daf29ee3`): sf-coffee classified 'developer'
(IsSandbox=False, Developer Edition)

**71/71 checks pass. 15 mutators, all caught.** 196 adversarial fixtures (193 recorded on disk,
3 minted at run time), 128 differential-fuzz cases. Toolchain: sf 2.144.6, node v24.11.1,
python 3.14.3.

### Why this entry is dated later than the run it describes

The run happened on 2026-08-02 and was never written down. Until then the newest entry here was
P7 — "59/59 checks pass. 11 mutators" — while the attestation directory already held a 71/71
release pass with 15 mutators. For three commits this log understated the thing it exists to
record, which is a gentler failure than overstating it and the same underlying defect: a number
written once and not re-derived. It was found by an external evaluation reading the artifacts
against the prose, not by any check, because no check compares this log's newest entry to the
newest attestation. That check does not exist yet and should.

### What this run does NOT cover

Three commits have landed since the attested tree, and the honest reading of this entry has to
name them:

| Commit | Change |
|---|---|
| `c73b53c` | P1-002 — first-party trust stopped being path-based; `validate.py` deliberately lost its interpreter exemption |
| `04d142a` | `/status` command |
| `a40b58f` | the product/roadmap evaluation and its defect punch list |

P1-002 is the one that matters: it changed which tools the gates trust, and it landed **after**
this run. So by the definition in `.claude/rules/validation.md` — current means the newest
all-PASS release entry whose TESTED tree equals the PARENT tree of the docs-only attestation
commit at the tip — **this entry is not current, and nothing here should be read as covering the
tree as it stands.** It records a real run at a named commit. That is all it records.

### One defect visible inside the attestation itself

`self_test.mutators_caught` is 15; `self_test.mutators` names 14. Both numbers are produced by
the same run, and they disagree because `torque-attest:61` harvests names by matching the literal
token `" mutator:"` in the self-test output, and the clean_ip fail-closed mutator's line does not
carry it. So the artifact counts a mutator it cannot name — a count and a list, derived from one
source, disagreeing in the file the repo offers a reader as evidence. Filed as D7 in
`docs/HANDOFF-DEFECTS-2026-08.md`; the fix is one word on one line in `validate.py`, and it is
blocked pending operator-present issuance because `validate.py` is a protected basename.

---

## P9 — capability · RED, and the red is self-inflicted · 2026-08-04

**No attestation.** `bin/torque-attest` refuses to write one for a run that is not a pass, so
there is no `attest-*.json` for this entry and there should not be. The newest attestation on
disk remains `attest-3c662cf5`, which is P8's and is older than this tree.

Commit `94ef337` (A1 applied) · **profile: capability · target `sf-coffee` · verdict: FAIL**

**67 of 68 checks pass. Self-test: FAILURE.**

### The one real failure

```
✗ clean_ip   FAIL   denied term in tracked file docs/MAINTAINER-MODE.md
```

A document written that day to argue that the safety machinery should not be switched off reached
for real identifying detail to make the risk concrete. In a public repository. The argument never
needed it: a count carries "a working laptop has a double-digit number of production orgs
authenticated" exactly as well as a list does, and only the list carries anything back to the
organisations named.

`clean_ip` caught it. No human did, and the reviewing session that wrote the file did not. It is
worth being precise about why that is the system working rather than a lucky catch: the leak was
in a *privacy-and-safety design document*, which is the last place anyone would think to look,
and the check does not depend on anyone thinking to look.

The second red line is a consequence, not a second defect:

```
✗ clean_ip historical-blob mutator: expected FAIL, got FAIL
```

That mutator asserts the failure detail names a historical blob. `clean_ip` was already failing
for an unrelated reason, so the mutator could not distinguish its own signal from the noise and
scored itself red. One leak took out both a check and that check's proof — a failing check makes
every check downstream of it less informative, which is an argument for fixing red immediately
rather than reading around it.

The working tree was corrected in the same session. The history was rewritten separately, because
a redaction that only touches the tip leaves the term reachable in the objects behind it, and
`clean_ip` scans the objects rather than the checkout. That is the whole reason it has a
historical-blob path at all.

Two things are worth keeping from this, and neither needs the specifics. Removing a term from
history is an operator action, outward-facing and destructive, and not the agent's to take. And
until it is done, no capability or release run can be all-PASS, correctly, because the repository
still carries what the check objects to. A green log above a repository in that state would be
worth less than a red one.

### What this run does establish

Every org-touching check passed live against a disposable Developer Edition org — the half of the
harness that no static run and no CI job can reach:

| Check | Result |
|---|---|
| `org_classify` | `sf-coffee` classified 'developer' (IsSandbox=False, Developer Edition) |
| `probe_cycle` | deploy → verify (field ok, FLS asserted) → purge → teardown; live residue 0, one `_del` tombstone in the 15-day queue as expected |
| `mass_update_cycle` | created → preview (exact 2) → update → verify → undo (restored); teardown by Id |
| `impact_bound_approval` | 43 Leads; within-scope proceeds and consumes, grown scope refused, unverifiable scope refused |
| `session_log_integrity` | written, reversible, redacted (session id + org id), 0600, parseable |
| `browser_render` | Lightning shell rendered live (`RENDER_OK Home | Salesforce`) |

It is also the first capability run to cover A1's fix: `shadow_cannot_escape_the_transaction`
now refuses eight disqualifying shapes by named reason, having previously been unable to fail at
all.

Two known defects were visible passing, both still blocked on operator-present issuance:
`image_manifest` reporting "0 manifest entries; all guide images verified" while verifying
nothing (B1), and `named_mutators_exist` deriving 14 names against `TOTAL_MUTATORS = 15` (D7).

### What it is not

One run, one disposable org, by the author, and **red**. It is retained as diagnostic. A later
all-PASS run supersedes it, and could not happen until the history carried no denied term — which
is the correct ordering, since the alternative is a green log sitting above a repository that
still contains what the check objects to.

---

## P10 — release · RED, and both reds were the harness misreading itself · 2026-08-05

Commit `32d3e759`, tree `70d16940`, working tree clean · **profile: release · target `sf-coffee`
(classified `developer`, IsSandbox=False, Developer Edition) · verdict: FAIL** · 538s
· 115 checks: 108 PASS, 2 FAIL, 5 N/A · artifact `harness/attest/attest-32d3e759.json`

The first release run an operator has performed at their own terminal rather than through an
agent session. That difference produced one of the two failures, and it is the more interesting
one.

### `uninstall_requires_an_operator` — FAIL, and the product was fine

> uninstalling enforcement is reachable from the agent tool surface: `--remove-shim`: exit 0;
> `--remove`: exit 0, said 'removed: 0 torque hook blocks'

It was not reachable. The check ran the uninstaller as a subprocess and asserted a refusal, on a
stated assumption: *this process is a descendant of the agent, so `lib.operator_present()` is
false here exactly as it is for any tool call.* True when an agent runs the harness. False when
an operator does — and a release attestation is precisely the run an operator performs. The
subprocess inherited a real login tty, the uninstall was legitimately authorised, and the check
reported the product broken.

The polarity is what makes it worth an entry. It passed in the context that mattered least and
failed in the one that mattered most; written the other way round it would have reported PASS
indefinitely while never once testing the agent case. Verified by hand from inside a session
before touching it: `install-gates --remove-shim` refuses with exit 2 and names operator
presence.

Fixed by making the context deterministic instead of inherited — `start_new_session=True` drops
the controlling terminal, and `_has_tty()` opens `/dev/tty`, so presence is false whoever
launched the harness. The acceptance direction was missing entirely and is now asserted
in-process against a stubbed `lib`, because refuse-always passes a refusal-only test and a login
session must not become forgeable to satisfy a check.

### `cache_poison_resistant` — FAIL, raised rather than judged

> check raised: module lib not in sys.modules

`deploy_directory_destructiveness_is_seen` pops `shellparse` and `lib` from `sys.modules` to
force a fresh import of the current source, and did not put them back. `cache_poison_resistant`
holds a reference to `lib` and calls `importlib.reload` on it, which requires the name still be
registered. Order-dependent, so it fails in a full run and passes in isolation — which is why it
had not been seen. Reproduced exactly, then fixed at the cause (the popper restores what it
borrowed) and at the consumer (a re-registration before reload, so the next check that forgets
costs nothing).

Neither failure was a gate defect. Both were the harness measuring itself wrongly, which is the
category this log is least able to catch by design — every other check here is aimed at the
product.

### What this run does establish

108 checks passed live against a disposable Developer Edition org, including the whole
org-touching half no static run reaches. The five N/A entries are honest: no shim installed on
this machine, no image manifest, no must-allow corpus configured, and two that report N/A
specifically because enforcement is not yet activated — `maintainer_edit_cannot_change_active_gate`
and `active_enforcement_is_anchor_owned` have nothing to measure until it is.

### What it is not

Red, and superseded by any later all-PASS release run. Retained as diagnostic. The two fixes land
in the commit that follows this entry, so the tree this attestation names is deliberately NOT the
tree they are on.

---

## P11 — release · GREEN, and the trust plane is measured rather than assumed · 2026-08-05

Commit `b99390ce`, tree `0d815117230b`, working tree clean · **profile: release · target
`sf-coffee` (classified `developer`, IsSandbox=False, Developer Edition) · verdict: PASS** · 432s
· 117 checks: **114 PASS, 0 FAIL, 3 N/A** · all 19 mutators caught, `redaction` included
· artifact `harness/attest/attest-b99390ce.json`

Supersedes P9 and P10. The first all-PASS release run since the trust plane was built, and the
first in which `maintainer_edit_cannot_change_active_gate` had anything to measure.

### What changed today, and none of it was found by reading

Seven defects, every one found by running Torque rather than inspecting it. Three external audit
lenses had been over this code and found none of them.

| | |
|---|---|
| `apex run test` classified as anonymous Apex | a refusal no operator could satisfy — no `--file`, so no digest to approve |
| M8: the protected-object floor | unreachable on delete-by-Id, the spelling the rules mandate |
| M8's MCP twin | the same delete refused through Bash and allowed through MCP |
| `install-gates --project` | the flag activation's own success message named did not exist |
| the enforcement manifest | keyed two different ways, so it could never report a match |
| `cache_poison_resistant` | raised rather than judged, because another check left `sys.modules` altered |
| `write-surface.py` | reported a path as writable that the gate refuses |

Two of the seven were introduced today, by this session, and caught the same way.

### The check that had never run

`maintainer_edit_cannot_change_active_gate` reported N/A from the day it was written, correctly:
enforcement was not activated, so there was nothing to measure and it said so rather than passing.
It now reads:

> a window-legal edit to `authorize_write` flips the workspace copy (exit 0) and leaves the active
> gate denying — enforcement is running from the anchor

That is P0-01 closed by measurement. The agent can still edit `hooks/` under a maintainer window;
the edit no longer changes what adjudicates a write.

### The three N/A, and why none is a pass

- `installed_shim_matches_its_source` — no exec-time shim on this machine. Nothing to compare
  rather than nothing wrong. **Without the shim the allowlist is advisory**, and the README says
  so in those words.
- `must_allow_corpus_has_no_shape_denials` — no corpus configured. Real commands carry client
  names and record Ids and cannot live in this repository; `TORQUE_ALLOW_CORPUS` points at one the
  operator keeps.
- `image_manifest` — no images claimed, so none verified. An empty loop is not evidence.

### What this run is evidence of, and what it is not

Every org-touching check passed live against a disposable Developer Edition org — the half no
static run and no CI job can reach. Classification was exercised across six disposable orgs, and
four of them classified perfectly non-production and were still refused for not being on the
allowlist: eligibility is not sufficient, which is the claim the design rests on.

It remains one run, one org, by the author. The evidence is a day old on a tool that is weeks old,
and the shim — the thing that makes the allowlist enforced rather than advisory — is not installed
on the machine that produced this log. Both are stated in the README rather than implied away.

---

## P12 — release · GREEN, after a documentation audit that found the shipped default was wrong · 2026-08-05

Commit `dab11c50`, tree `bcfe38e45d10`, working tree clean · **profile: release · target
`sf-coffee` (classified `developer`, IsSandbox=False, Developer Edition) · verdict: PASS** · 569s
· 117 checks: **114 PASS, 0 FAIL, 3 N/A** · all 19 mutators caught, `redaction` included
· artifact `harness/attest/attest-dab11c50.json`

Supersedes P11. Same green, one tree later, and the tree in between is why this entry exists.

### The audit that produced it

P11 was green and the documentation describing it was not. `activate-enforcement` — the command
that closes P0-01 — appeared in **zero tracked documents**. `org-safety.md` carries the layer model
and had no layer for it. The guide mentioned it nowhere across 22 pages. `MAINTAINER-MODE.md`,
whose entire subject is what a maintainer window may do, did not say the window's power is now
bounded by it. A safety property a reader cannot find is not one they can rely on.

### What the audit found that was worse than stale prose

The tracked `.claude/settings.json` named `$HOME/.torque/enforcement/current/hooks/`, which does
not exist on a machine that has never activated. Measured rather than assumed: the interpreter
exits **2** for a missing script, and the enforcement contract reads exit 2 as deny. **A fresh
clone of this repository would have blocked every gated tool call** with an opaque `can't open
file`, while the guide promised "nothing further is needed".

Fail-closed rather than unsafe, and still a wall for anyone trying the tool for the first time.
`install-gates --project` now writes an **untracked** `settings.local.json`: the committed
registration stays workspace-pointing so a clone works with no setup, the hardened registration
lives beside it without entering the repository, and the working tree stays clean — which matters
because a dirty tree can neither be re-activated nor anchor an attestation.

The trust-plane checks now read the **effective** registration, the merge of both files. Reading
only the tracked one would report the portable default and call a correctly-hardened machine
unprotected.

### Numbers `claimed_counts` could not see

It re-derives counts from the artifacts they describe, and it checks one phrasing. These used
others and had drifted: the guide said "196 adversarial fixtures" twice (237), "34 entries" twice
for a 44-entry catalogue, and `release (98)` for a 117-check profile. `ROADMAP.md` said 216
fixtures and quoted retrieval as 94/86/85 against measured values of 95% matched, 88% surfaced over
81 cases, 88% precision over 34 negatives — so the precision headroom is three points rather than
none. The allow share is now stated as measured: 67 of 237, 28%.

`claimed_counts` did catch the guide growing to 22 pages, which is the half of it that works.

### What this run is evidence of, and what it is not

Every org-touching check passed live against a disposable Developer Edition org. Enforcement runs
from the trust anchor, and `maintainer_edit_cannot_change_active_gate` measures that rather than
assuming it.

Still one run, one org, by the author, days old on a tool that is weeks old. The exec-time shim is
not installed on this machine, so `installed_shim_matches_its_source` reports N/A and the allowlist
is advisory here rather than enforced. The README says that in those words.
