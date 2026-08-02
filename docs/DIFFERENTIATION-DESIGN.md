# What would make Torque hard to replace — design under review

Status: **proposal, under adversarial review.** Nothing here is built. Written to be attacked.

---

## The premise, stated so it can be disputed

Torque's differentiation today rests on three things: gates that adjudicate at the moment of the
operation, a catalogue where entries declare how they are known and re-verify against a live org,
and a harness that proves its own checks can fail.

The claim under review is that **none of that is where the durable advantage lies**, and that the
durable advantage is per-org knowledge that improves with use and can prove what it knows.

### Sub-claim 1 — general Salesforce knowledge is not a moat

Any frontier model already knows the order of execution and the governor limits, and the next one
knows them better at no cost to its vendor. A catalogue racing to 500 entries competes on the axis
where competition improves for free.

What is defensible about the catalogue is not its contents but two properties around them: entries
that **re-verify** and fail the build when they stop being true, and entries that **arrive at the
decision** rather than sitting in a file. 39 verified, delivered entries beat 500 asserted ones.

**Reframe:** the catalogue's job is not to be an encyclopedia but to be the set of QUESTIONS worth
asking about any org — a checklist generator that tells the per-org layer where to look.

### Sub-claim 2 — three things a model cannot have

1. **What is true of THIS org right now.** 400 validation rules on that sandbox; `Status__c`
   restricted; the integration user lacking ModifyAllData; Tuesday's deploy hitting the API
   ceiling. In no training set, changing weekly.
2. **What went wrong HERE before**, and what the fix turned out to be.
3. **Proof a claim still holds.** A model asserts; Torque can check.

Per-org context is the only axis where "the more it is used the better it gets" is literally true
rather than aspirational.

### Sub-claim 3 — the thing to build is a profile, not a log

Not a lesson log. A **per-org profile that is derived, dated and re-verified**: automation per
object, restricted fields, API budget behaviour, which validation rules actually fire, what
typically fails here. Every fact carries provenance and freshness, is re-checked on a cadence, and
degrades to "unconfirmed since March" rather than silently persisting.

---

## The mechanism, and the three ways this class of system dies

**Noise.** Auto-capture on every failure produces a queue nobody reads, and then the system is
inert while still claiming to learn. The narrow signal worth capturing: an operation that FAILED,
then the same shape SUCCEEDED — the delta is the lesson; everything else is a log line.

**Confident stale memory, which is worse than none.** A fact learned in March and asserted in
August is a liability. Every per-org fact needs what a catalogue entry has: how it was learned,
when it was last confirmed, and a way to re-check it. Facts that cannot be re-checked decay and
say so. This is the load-bearing decision — without it the system lies with growing confidence.

**Promotion without a gate.** A captured observation is a hypothesis. It needs corroboration —
seen twice, or confirmed live — before being stated to the operator as fact.

Org-Id keying already exists and generalises: a sandbox refresh mints a new Id, so the memory
empties exactly when the org it described stopped existing.

---

## It needs a number, or it is marketing

"Gets better with use" is unfalsifiable as stated. Proposed measures, in the same discipline as
`retrieval_quality`:

- of the facts the profile asserts about an org, what share still verify?
- does the share of operations where Torque had something ORG-SPECIFIC to say rise with use?
- what is the median age of an asserted fact, and how much of the profile is stale?

---

## Constraints that are not negotiable

- **Privacy.** Per-org data is client data. Never committed, never leaves the machine, 0600.
  The existing posture must survive this expansion, not be reopened by it.
- **The line between layers.** Platform truth goes in the catalogue and must be verifiable
  anywhere; org truth goes in the profile and is only ever true of one org. When they disagree,
  the org wins.

---

## What the reviewers are asked

1. Is sub-claim 1 wrong? Is there a version of general knowledge that IS defensible?
2. Where does the profile mechanism break in practice, before it delivers value?
3. What does this actually beat, and what already does it better?
4. What is missing — a differentiator neither the current tool nor this plan contains?
