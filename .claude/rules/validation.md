# Validation — the contract

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

DEGRADED entries are diagnostic and retained; a later all-PASS entry supersedes them.
"Current" means the newest all-PASS release entry whose TESTED tree equals the PARENT
tree of the mechanically-verified docs-only attestation commit at the tip.

Publication requires a current all-PASS release entry. Named exception: the M1 draft —
all-PASS capability run (excepted-org check as a HARD-FAIL included), no images, and a
phase-status table listing any WARNs verbatim.
