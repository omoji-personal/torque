---
description: Orient at the start of a Torque session — state, open work, and what not to trust
---

Orient yourself before doing anything. Read, then report; do not start work in this turn.

## 1. Read these, in this order

**Tracked, present on every clone. Always read these:**

- `ROADMAP.md` — the four steps and, more usefully, what was dropped and why
- `harness/VALIDATION.md` — read the LAST entry. It states the profile, verdict, org
  classification and what the run does and does not establish
- the newest `harness/attest/*.json` — commit, tree, working-tree cleanliness, every check
  outcome, every mutator. This is the evidence; VALIDATION.md is the narration of it
- `docs/HANDOFF-DEFECTS-2026-08.md` — the open punch list, if it is still there

**Operator-local, gitignored, present on ONE machine. Read if they exist, and say so if not:**

- `local/HANDOFF-AUDIT.md` — current state, what is closed, what is open, lens availability
- `local/audit-round9/sol-verify.md` — findings adjudicated with runnable evidence and patches
  (skim the verdicts; do not read every diff yet)

C7: this command used to instruct a read of the two `local/` files unconditionally. They are
gitignored, so on any clone but one the session opened by failing to read files it had just been
told were the source of truth, and had no fallback. Orientation has to work from tracked state
first, with the private context as an enrichment. **If the local files are absent, do not
improvise a substitute — say which context is missing and what that means you cannot know.**

## 2. Establish the tree state yourself

```
git log --oneline -5
git status --porcelain
```

**Targeted checks DO run from inside a session** and are the fastest way to establish a fact
rather than assume it:

```
python3 harness/validate.py --only <check-name>
```

**Profile runs do NOT.** `validate.py` lost its interpreter exemption when P1-002 closed, and any
invocation carrying `--target-org` is refused as a Salesforce operation via an interpreter —
correctly, because `probe_cycle` deploys and deletes metadata and was never read-only. Ask the
operator to run those with the `!` prefix so the output lands in the conversation, substituting
their own disposable org:

```
! python3 harness/validate.py --profile release --target-org <disposable-org>
```

**Do not run `--self-test` while other agent sessions are live.** It mutates hook sources in
place and restores them in a `finally`; a concurrent session hitting a gate mid-mutation sees
transient refusals, or worse, a briefly neutered guard. That is P1-007. To exercise it safely,
copy the tree somewhere disposable and run it there — `.git` included, or three mutators that
plant a tracked file will report false failures.

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
