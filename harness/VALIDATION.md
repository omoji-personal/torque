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
