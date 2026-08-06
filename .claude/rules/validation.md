# Validation — the contract

ENFORCEMENT: harness-enforced (deliverable_coverage)

Profiles: **release ⊇ capability ⊇ static** — every lower-profile check runs in the
higher profile. `--self-test` (fixture-based, offline) runs AS PART OF static and re-runs
in capability and release.

| Outcome | meaning |
|---|---|
| PASS | green |
| FAIL | red — the run is red |
| WARN | does not break all-PASS; reproduced verbatim in the attestation and any phase-status table; promoted to FAIL in release where a check says so |
| SKIP/BLOCKED | non-green: allowed only via per-check `--allow-skip=<id>:<reason>`; the run verdict becomes DEGRADED; release refuses `--allow-skips` entirely |

**Done-gate is the surface verdict:** a change is done when every check mapped to its
changed surface passed. Whole-profile PASS is mandatory only for release and publication.

- CAPABILITY changes (hooks, checks, org-touching skills) require capability-profile
  surface-green live on the validation org.
- STATIC changes (prose, docs) require the static profile only.

## Test both directions, or you have tested neither

**Proving a control REFUSES is not proving it ACCEPTS.** A gate that denies everything passes
every deny fixture. Every assertion that something is blocked needs a paired assertion that the
legitimate form of the same thing goes through — and the allow case must look like real use, not
its simplest instance.

This has now cost this repo four times: `operator_presence_can_succeed` (refusal proven,
acceptance not); `torque init` (same); 193 gate fixtures whose 44 allow cases contained four
copies of `SELECT Id FROM Account` and no WHERE clause, missing a defect that denied 71% of six
months of real commands; and `bin/torque` shipping to close a gap while nothing put it on PATH,
so the command every deny message names was `command not found`.

Corollaries, each learned the same way:
- A check that examined nothing reports NA, never PASS. An empty corpus passes every assertion.
- Agreement at the wrong verdict is not agreement. Two paths that both refuse a legal command
  agree perfectly and are both wrong.
- A mutation must exercise the guard it names. If either half of a two-part regression is inert
  alone, mutate both — a one-part mutation reports a vacuous PASS.

DEGRADED entries are diagnostic and retained; a later all-PASS entry supersedes them.
"Current" means the newest all-PASS release entry whose TESTED tree equals the PARENT
tree of the mechanically-verified docs-only attestation commit at the tip.

Publication requires a current all-PASS release entry. Named exception: the M1 draft —
all-PASS capability run (excepted-org check as a HARD-FAIL included), no images, and a
phase-status table listing any WARNs verbatim.
