---
description: Orient at the start of a Torque session — state, open work, and what not to trust
---

Orient yourself before doing anything. Read, then report; do not start work in this turn.

## 1. Read these, in this order

- `local/HANDOFF-AUDIT.md` — current state, what is closed, what is open, lens availability
- `ROADMAP.md` — the four steps and, more usefully, what was dropped and why
- `local/audit-round9/sol-verify.md` — findings adjudicated with runnable evidence and patches
  (skim the verdicts; do not read every diff yet)

## 2. Establish the tree state yourself

```
git log --oneline -5
git status --porcelain
```

Do NOT run the harness. `validate.py` deliberately lost its interpreter exemption when P1-002
closed — `probe_cycle` deploys and deletes metadata, so it was never read-only. The gates in this
repo are LIVE and will refuse it. If you need a harness run, ask the operator to run it with the
`!` prefix so the output lands in the conversation:

```
! python3 harness/validate.py --profile release --target-org sf-coffee
! python3 harness/validate.py --self-test
```

`checkup`, `blast-radius` and `log` are the declared read-only tools and work from inside a
session.

## 3. Report back, briefly

- where the tree is (commit, clean or not, last few commits)
- what is open, in priority order, from the handoff
- anything in the handoff that has gone stale against what you actually observed — say so
  plainly rather than repeating the document

## 4. Then stop and ask what to work on

## What this repo expects of you

Four things, learned expensively rather than adopted:

1. **Reproduce before fixing.** External reviewers this project has used were repeatedly right
   about the shape and wrong about the specifics — one reported 16 unreachable catalogue entries
   when there were 7. A finding that no longer reproduces is worth more as a correction than a fix.
2. **Listing a risk is not gating it.** The worst defect found here was in a tool whose own
   docstring accurately described the danger it failed to enforce.
3. **Empty is not an answer.** A failed query, an unparseable response, a source that returned
   nothing — none of those mean "none". That single confusion has now appeared in the parser,
   in blast-radius, in a probe script and in the catalogue verifier.
4. **Every fix ships with a check, and the check must be able to fail.** If you cannot make it
   fail on purpose, it is not evidence.
