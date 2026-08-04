# Handoff — defect punch list from the 2026-08-03 evaluation

Work order for a local Claude Code session with live org access. Context and rationale:
`docs/EVAL-2026-08.md`. Every location below was verified against commit `04d142a` before
being written down.

---

## STATUS as of 2026-08-04 — all items closed except C6's live half

**A1 A2 B1 B2 B3 B4 B5 C1 C2 C3 C4 C5 C7 C8 C9 D1 D2 D3 D4 D5 D6 D7 D8 E1** — closed, each with
a check that fails when it regresses, each verified in both directions before commit.

**C6** — the zero-rows SKIP is implemented and the check no longer reddens on an org with no
Leads. The positive path still needs a run against an org that has one.

Line numbers below are as-written on `04d142a` and have moved. Two entries did not survive
contact and are corrected rather than silently fixed:

- **C3** — the claim that the un-mutated gate *allows* the hardcoded attack off macOS could not
  be reproduced. With `TORQUE_ANCHOR` relocated the gate still denies, so it refuses that string
  for a broader reason than reaching the real anchor. Not disproven either: simulating a
  filesystem without `/Users` is not possible from macOS. The fix landed on other grounds.
- **B1's remedy** was "absent manifest + zero images → N/A". Correct, and it understated the
  defect: three independent conditions each made the check vacuous, so it needed all three to be
  false before a line of its logic ran.

Two defects were found while fixing others, neither in this list:

- `bin/torque-attest` never imported `os`, so a module-level `os.environ.get` added for C2 would
  have raised `NameError` on every attestation. `py_compile` passed it; compiling is not
  executing.
- `claimed_counts` matched `"N checks"` but never the per-profile breakdown beside it, so moving
  one check between profiles left two of three numbers in the same sentence unverifiable.

---

**Rules of engagement (the repo's own):**

- Every fix ships with its proof — a fixture, a mutator arm, or a check that fails when the
  fix regresses. A fix without a proof is not done.
- Surface verdicts per `.claude/rules/validation.md`: items marked **CAPABILITY** touch
  hooks/checks/org-touching tools and need a capability-profile surface-green run live
  (`--target-org` a disposable non-production org). Items marked **STATIC** need the static
  profile only.
- Work the groups in order (A → E). Within a group, order is by risk.
- Commit style: one defect (or one tightly-related cluster) per commit, message in the repo's
  voice.

---

## A. Safety-relevant

**A1 · `bin/torque-shadow:94` — dead protected-object guard.** CAPABILITY.
`lib.protected_object_hits(body_src) if hasattr(lib, "protected_object_hits") else []` —
the function exists nowhere (`lib.py` has `protected_objects()` at :666 and
`is_protected_target()` at :1023), so the `hasattr` is permanently False and the refusal
printed at :96 is unreachable, ten lines below the comment promising it.
*Fix:* scan the Apex body tokens against `lib.protected_objects()`. Note
`destructive_data_gate.py` already shields Apex bodies — prefer sharing one implementation
over writing a twin (`no_divergent_twins` is the house rule for exactly this).
*Proof:* add a protected-object arm to `shadow_cannot_escape_the_transaction`
(`check_kb.py`), plus a falsification: neuter the guard, require the check to go red — the
current check only tests the escape regexes, which is why this survived.

**A2 · `bin/torque-install-gates:35-37` — not idempotent, docstring says "Idempotent."**
STATIC (installer). The PreToolUse list is deduped (`:19` strips prior torque registrations)
but the PostToolUse observer is `.append`ed unconditionally — re-running duplicates it.
*Fix:* apply the same strip-then-append to `PostToolUse`.
*Proof:* extend `installer_roundtrip` to run install twice and assert exactly one observer
registration.

## B. Checks that cannot fail / broken derivations

**B1 · `harness/checks/check_p5_release.py:45` `image_manifest` — catastrophe-class check
that cannot fail.** STATIC. Manifest file absent → `{"images": []}`; `guide/` contains zero
images; the loop never executes; PASS reads "0 manifest entries; all guide images verified".
*Fix:* absent manifest + zero images → N/A (nothing claimed, nothing to verify) — not PASS
with a verification message. Manifest absent while images exist → FAIL (already the effective
behavior via the not-in-manifest branch; make the absent-manifest case explicit).
*Proof:* falsification — plant a temp image under `guide/`, require FAIL, remove it. Cheap
enough to run inline like the other planted-artifact mutators.

**B2 · `harness/checks/check_kb.py:1905-1926` `public_description_accurate` — metric
derivation broken three ways.** STATIC.
(a) `mutators` splits `validate.py` on the string `"REGRESSIONS"` — grep count in that file:
0 — so the split degenerates and the count derives to 0.
(b) `fuzz` counts `^\s*[A-Z_]+\s*=\s*\(` in `differential_fuzz.py` — matches nothing, derives 0.
(c) the `m = search(r"(\d+)\s+generated cases", …)` result is never used, and `128` is
hardcoded into `real` instead.
Net: `real = {0, 39, 50, 68, 71, 128, 193, 196}` — the check would bless a public description
claiming "0" and fail a correct "15".
*Fix:* derive mutators from `TOTAL_MUTATORS` (validate.py:390) by import or a targeted regex
on that assignment; derive the fuzz count from `differential_fuzz.py`'s actual case
generation; delete the hardcoded 128 and the dead `m`.
*Proof:* a falsification seam: feed the checker a description containing "0 mutators" and
require FAIL; "15 mutators" must PASS.

**B3 · `check_kb.py:939` `claimed_counts` + `scripts/sync-counts.py:28` — ROADMAP.md is
outside both.** STATIC. The scan/rewrite set is (`guide/torque-guide.html`, `README.md`,
`bin/torque-demo`, `bin/torque-init`) in both places; the one wrong count in the repo
("66 checks", ROADMAP.md:106) lives in the one prose file neither tool covers.
*Fix:* add `ROADMAP.md` to both lists; correct the stale numbers it carries (66 → current
profile totals; also re-quote the retrieval numbers per B4).
*Scope note:* do **not** add `docs/EVAL-2026-08.md` or this file — they are dated snapshots
whose numbers are true of a named commit, not living claims. If a distinction is worth
encoding, it is "living surfaces are scanned; dated documents state their commit and are
exempt."
*Proof:* `claimed_counts` itself, now failing on a planted wrong count in ROADMAP.md.

**B4 · ROADMAP.md:106-107 — three number problems in one paragraph.** STATIC.
(a) "66 checks" — stale (B3 automates).
(b) "196 adversarial fixtures … are a year of adversarial rounds" — the public git history
spans three days; rephrase to something checkable ("fourteen adversarial rounds across three
audit lenses" or drop the duration).
(c) "retrieval measured at 94% recall / 85% precision" — 94% is *matched* recall; *surfaced*
recall (post the 2-slot display limit — what an operator sees) is 86.4% and has no floor;
precision is 85.3% against a FAIL floor of 85.0%. Either quote surfaced recall, or quote both
with labels. Consider a floor for surfaced recall while in there
(`check_kb.py:871` gates matched only).

**B5 · `harness/VALIDATION.md` tail — the log's newest entry is stale.** STATIC. P7 says
"59/59 checks pass. 11 mutators" while the newest attestation (`attest-3c662cf5`,
2026-08-02T15:54) records 71/71 and 15. The run after P7 was never logged.
*Fix:* append a new entry rendered from the newest attestation (the honest fix — the log
missed a run), and consider a check that the newest entry's counts match the newest
attestation so the log cannot silently fall behind again.

## C. Portability and fresh-user experience

**C1 · `check_p4_browser.py:31` — Chromium found only at `~/Library/Caches/ms-playwright`
(macOS).** CAPABILITY. On Linux/Windows the check always SKIPs even with Playwright
installed, so the release verdict is DEGRADED-forever off-macOS; README:177 does not say so.
*Fix:* honor `PLAYWRIGHT_BROWSERS_PATH`, then per-OS defaults (`~/.cache/ms-playwright` on
Linux, `%LOCALAPPDATA%\ms-playwright` on Windows), or ask Playwright itself.
Also `:35`/`:58`: the SKIP messages hardcode "BLOCKED 2026-07-31" — derive the date.

**C2 · `harness/validate.py:20` + `bin/torque-attest:28` — operator denylist hardcoded to
`~/Desktop/torque-planning/denylist.txt`.** STATIC. On any other machine attest records
`"ABSENT"` and operator-mode detection keys off the author's Desktop layout.
*Fix:* env override (e.g. `TORQUE_DENYLIST`) with the current path as fallback; same
resolution in both files (one boundary, one implementation).

**C3 · `validate.py:553` — the `_glob_reaches` mutator attack is author-machine-shaped.**
STATIC (mutator quality). `cat /Users/**/omidmojtahedi/.[t]orque/sec[r]et` — `/Users` does
not exist on Linux, so the *un-mutated* gate also allows and the mutator "catches" without
proving anything off macOS. Same family: `:468` and `:537` hardcode `~/.torque` and go
vacuous under a relocated `TORQUE_ANCHOR`.
*Fix:* build attack paths from the resolved anchor/home at runtime, the way
`run_gate_fixtures.py:110-121` already rewrites fixture paths — reuse that mechanism.
*Proof:* on Linux, the un-mutated gate must DENY the constructed path (assert before
mutating), else the mutator run is vacuous by its own definition.

**C4 · `validate.py:196` + `:485` — `sf-coffee` hardcodes.** STATIC. The `org_classify`
failure message names sf-coffee regardless of `--target-org`; the destructive-token mutator
targets `--target-org sf-coffee` on every machine.
*Fix:* use the passed target in both.

**C5 · `check_p1_gates.py:220` — capability without `--target-org` is a crash-shaped FAIL.**
STATIC. `env={**os.environ, "TORQUE_TEST_ORG": target}` with `target=None` raises TypeError →
caught upstream → FAIL. The house taxonomy says this is a SKIP/BLOCKED with a reason.
*Fix:* guard for no target and return the honest non-green outcome.

**C6 · `check_kb.py:1812` `impact_bound_approval` — reddens on an org with zero Leads.**
CAPABILITY. `mint(max(0, live-1))` with `live=0` → no drift → the gate allows → check FAILs.
A fresh DE org with Leads cleared fails the release for a harness reason.
*Fix:* create a flagged probe record for the count (delete by Id after, per house rules), or
SKIP with a dated reason when the object is empty.

**C7 · `.claude/commands/status.md` — the newest feature reads two gitignored files.**
STATIC. `local/HANDOFF-AUDIT.md` and `local/audit-round9/sol-verify.md` exist on one machine.
*Fix:* degrade gracefully — orient from tracked state (VALIDATION.md tail, newest
`harness/attest/*.json`, `git log`/`status`) and say explicitly which local context is absent
rather than instructing reads that cannot succeed on any clone.

**C8 · `bin/torque-blast-radius:79` — `--operation insert` accepted by argparse, then
`raise Unknown` unconditionally.** STATIC. Either implement scope-from-file for insert or
remove the choice; an accepted flag that always errors is a stub wearing an interface.
Also: document (in `--help` and the README section) that exit 3 is the *expected* outcome on
orgs whose parents carry roll-up summaries — every rollup line is suffixed UNDETERMINED by
design, so a reader must not interpret exit 3 as tool failure.

**C9 · `check_kb.py:2497` (and `:2521`) — `guards_share_no_blind_assumption` itself carries a
blind assumption: the repo lives under `$HOME`.** STATIC. **Reproduced live** in the review
container (repo at `/home/user/torque`, different HOME): the tilde spelling is built as
`"~/" + str(ROOT).replace(str(Path.home()) + "/", "")`, which replaces nothing when ROOT is
not under HOME, yielding `~//home/user/torque/hooks/lib.py` — a string that tilde-expands to
a *different* file. `is_protected_target` correctly declines to match it, and the check
reports that correctness as `FAIL: missed the tilde home form`, turning the whole static
verdict red. On the author's machine (repo under `~`) it passes, which is how it survived;
note GitHub Actions checkouts also live under HOME, so E1's CI would *not* catch this — only
containers with the repo outside HOME do.
*Fix:* construct the tilde form only when `ROOT.is_relative_to(Path.home())`; otherwise skip
that one spelling with a stated reason (it does not exist on such a machine).
*Proof:* the sweep still exercises the tilde form when the layout allows it; a run with the
repo outside HOME goes green without weakening the other nine spellings.

## D. Claims and docs consistency (small, fast)

**D1 · `TOOLCHAIN.md`** — says `harness/checks/cli-write-surface.json` is "committed" and
re-derived "at every preflight"; the file does not exist and validate.py has no preflight
stage. Either build the derivation (it is a good idea — the write-surface list currently
lives in code) or reword to the truth. Same file: Python "≥ 3.11" vs README "≥ 3.8" vs
`torque-init:44` enforcing (3,8) — pick one floor; `python_floor_is_real` reads README only,
so the contradiction is invisible to the harness.

**D2 · `.claude/rules/browser-testing.md`** — MFA line says the 1 July date "was withdrawn";
it was *paused* (security-key re-registration bug) and enforcement restarted staggered from
July 20, 2026. Reword; the operational rule (frontdoor, never UI login) stands.

**D3 · `bin/torque-checkup:7` and `:143`** — "Nine entries carry one" / "two of the nine";
16 entries carry `detect:` at `04d142a`. Derive or de-number the docstring.

**D4 · Stale inline counts** — `check_p1_gates.py:216` "37 adversarial + legit fixtures"
(193+3 real); `run_gate_fixtures.py:37` "~125 fixtures"; `check_p5_release.py:22` "the
128-fixture check" (128 is the fuzz-case count, not fixtures — the exact mislabel-class
defect DESCRIBING-TORQUE.md documents). De-number comments or derive.

**D5 · `.gitignore:11-13`** — comment names `scripts/kb-fetch.py` (now
`scripts/salesforce-docs/ingest.py`) and `knowledge/cache/` (actual cache:
`knowledge/salesforce-docs/`, which is *committed* index material). Align comment with
reality so the privacy rationale reads true.

**D6 · `check_p1_gates.py:133` `local_hygiene`** — registered profile "capability" but does
no org work (scans `local/` modes and secret shapes). Move to static so `local/` is scanned
on every run.

**D7 · `check_kb.py:904` `named_mutators_exist`** — derives mutator names from validate.py's
print strings and misses the clean-ip fail-closed mutator (its line lacks the token the regex
keys on). Tighten the derivation or tag the line.

**D8 · `harness/tests/gate_fixtures_lessons.json`** — loaded (guarded) at
`run_gate_fixtures.py:102`, absent from the tree. Create the empty file with a header comment
or drop the load; a guarded load of a never-existing file is dead weight either way.

## E. CI

**E1 · `.github/workflows/validate.yml`** — static profile on every push and PR. STATIC.
No org, no secrets: `python3 harness/validate.py --profile static`.
Known risks to resolve while building it, honestly rather than by skipping:
- The container needs `bash` and probably the `sf` CLI on PATH (~30 static checks spawn the
  real gates, which may shell to `sf` on some paths; fixtures should deny pre-classification
  since no allowlist exists in CI, but verify rather than assume — run the workflow once and
  read every outcome).
- `differential_fuzz` builds its own `sf` stub and needs real bash — fine on ubuntu-latest.
- `clean_ip` reports operator-only (denylist absent) — expected N/A, must not fail CI.
- `public_description_accurate` needs `gh` — expected N/A without auth.
- Self-test mutates hook sources in place with restore logic — confirm a clean tree at end of
  job (`git status --porcelain` must be empty) so residue can never merge silently.
Follow-on (separate, optional): a scheduled capability run against a dedicated DE org via
JWT auth, secrets in Actions — only after static CI is boringly green.

---

## Explicitly NOT in this handoff — operator decisions, not tasks

Do not drift into these while working the list; they are called out in
`docs/EVAL-2026-08.md` §6-7 and need a deliberate yes from the operator first:

1. The launch experiment (five named practitioners, measured).
2. The PATH shim (promote from v2; exec-time classification on post-expansion argv).
3. The completion-gate product decision (verification-led positioning).
4. Claude Code plugin packaging.

## Definition of done for this handoff

- Groups A-D: every item fixed-with-proof or explicitly rejected with a reason in the commit
  message (a considered "won't fix, because" is a valid outcome; silence is not).
- Group E: static CI green on the default branch.
- A capability-profile run live against a disposable org, all-PASS (or DEGRADED with only the
  documented environment skips), attested.
- `harness/VALIDATION.md` gains an entry for that run (B5 makes this current again).
