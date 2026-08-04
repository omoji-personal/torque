# Roadmap

What is next, in order, and — more usefully — what was considered and dropped.

Everything below came out of an adversarial design review: two independent frontier models briefed
to attack the plan rather than improve it, one on mechanism and one on competitive reality. Both
rejected the thesis I started with. That is why the "dropped" section is first: it is the part
that took work to learn.

---

## Dropped, with reasons

**A per-org knowledge profile that compounds with use.** This was the plan. Two reviewers killed
it independently.

- Salto already models org configuration as a version-controlled, dependency-mapped graph —
  reviewed coverage of the idea: **85–90%**, and deeper than CLI queries reach.
- The valuable facts cannot be cheaply re-verified. A useful subset of org state re-checks fine;
  the experiential parts degrade into timestamped anecdotes, which is exactly what this project
  refuses to ship elsewhere.
- And it contained a contradiction: knowledge cannot both compound durably and be keyed to clear
  on a sandbox refresh. Both were stated as virtues. Only one can be.

**Racing the catalogue to hundreds of entries.** General Salesforce prose is not a differentiator;
any frontier model has it and the next one has it better. The refinement worth keeping, from the
mechanism review: *executable* general knowledge — knowledge that reaches the decision, carries
counterexamples, is tested against a live org and is protected by an adversarial harness — can be.
So the catalogue grows only where an entry can fire and verify.

**Competing with release-management platforms.** Gearset, Copado and Salto do environment
pipelines for humans through a UI. Torque runs inside an agent's tool-call loop. Different
consumer, different problem; fighting them on their ground loses on every axis.

---

## The problem actually being solved

Not that models lack Salesforce knowledge — they have it. Three failures survive knowing:

1. **Trial and error against a live org.** An agent guesses, fails, adjusts, retries. Every retry
   is a real mutation.
2. **Incomplete intent.** A field deploys and is invisible, because a field alone is not a feature
   — it needs field-level security, and usually a layout.
3. **Declaring done on the wrong signal.** Deploy status green, tests green, feature absent for
   every real user.

---

## Next, in order

### 1. Completeness closure
The catalogue already encodes "X alone does not work" — `fls-not-automatic` is a closure rule
filed as a hazard. Turning that into *"you asked for X; here is the set X actually requires"* is a
different query over data that already exists. First because it is nearly free and it de-risks
what follows.

### 2. Shadow execution
`Savepoint` → the real DML → the real runtime errors → `Database.rollback`. The trial-and-error
happens inside a transaction that never commits. **Mechanism already proven against a live org:
an insert observed, a genuine `REQUIRED_FIELD_MISSING` captured, residue zero.**

What remains is not the mechanism but the honesty around it: establish by test — not by assertion
— what does *not* roll back (callouts, `@future`/queueable, platform events, change data capture),
how governor limits accumulate inside the shadow transaction, and a hard refusal against
production.

### 3. The completion gate
A verified / not-verified ledger, ending in the browser under a **non-admin** profile — admin sees
everything and proves nothing. The same discipline the harness already applies, where a skip is
never allowed to read as a pass, applied to the word *done*.

Gated on three questions being answered first, because they decide whether it survives use: can it
render as a non-admin profile, what is the real wall-clock cost, and how flaky is it. A completion
gate that costs two minutes gets routed around, which is worse than not having one.

### 4. Proof-carrying operations
The unifying frame: every meaningful operation carries preconditions, predicted impact,
authorization bound to that impact, postconditions, unknowns, and an evidence receipt. Four of the
six exist already — impact-bound approval tokens and attestations are built. This step is mostly
integration, and it is what turns the pieces into one claim.

---

## Continuous, not sequenced

**The adversarial assurance corpus is the asset.** The observation that changed my mind, from the
mechanism review: the catalogue's facts are copyable; the evidence that the gates, the retrieval
and the verifiers fail correctly under hostile variation is not. 196 bypass fixtures, 34 negative
retrieval cases, 17 mutators, the live experiments and the verifier-falsification seams are what
the adversarial rounds left behind.

So new capability ships with its bypass fixtures and its mutator, or it does not ship.

**Documentation ingestion continues, demoted.** The pipeline exists and is cheap to run; it
grounds entries in Salesforce's own text instead of recall. It is not the differentiator.

---

## Where it is honest to say this stands

Steps 1 and 2 sharpen what Torque already is. Steps 3 and 4 change what it is *for* — from an
operations layer to the thing that will not let an agent claim done. That is a narrower and more
distinctive position, and it is a product decision rather than an engineering one.

The current state, measured rather than claimed: 85 checks (65 static, 82 capability, 85 release),
17 mutators, 196 adversarial fixtures, and retrieval measured against an evaluation set written by
someone other than the author of the thing being measured — 94% *matched* recall, 86% *surfaced*
recall, 85% precision over 34 negatives.

Both recall numbers, because they answer different questions and quoting only the first flatters
the tool: MATCHED is whether the entry's triggers fired at all; SURFACED is whether it survived the
two-slot display limit and actually reached the operator. The 8-point gap is a capacity limit
rather than a mis-ranking, and only matched recall currently has a FAIL floor. Precision sits at
85% against a floor of 85% — one more false positive turns the build red, which is the intended
tension and worth stating rather than discovering.
