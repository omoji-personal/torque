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

### 1. Completeness closure — shipped 2026-08-04
The catalogue already encodes "X alone does not work" — `fls-not-automatic` is a closure rule
filed as a hazard. Turning that into *"you asked for X; here is the set X actually requires"* is a
different query over data that already exists.

It was already half-built and quietly broken in three ways, each found by testing rather than
reading:

- **An empty requirement set meant two different things.** A bulk update matched seven catalogue
  entries, none in requirement form, and the gate printed nothing — so an agent that saw no
  `TORQUE NEEDS` line concluded there was none. `closure_report` now separates "nothing matched"
  from "matched, and nobody has recorded what it requires", and the gate says which.
- **A delete was told it needed field-level security.** `fls-not-automatic` carried a trigger
  matching the metadata *type* regardless of the verb. A requirement set carrying things the
  operation does not require misleads as much as one that omits things, and errs toward doing
  more work, so it is less likely to be questioned.
- **Every legacy `force:` spelling reached nothing.** The gate authorizes both spellings — the
  destructive classifier pairs each modern shape with its `force:` twin — but the catalogue's
  triggers were written against modern wording only. Five operation pairs tested, legacy reached
  zero entries five times out of five: correctly gated, and told nothing. `LEGACY_TO_MODERN` in
  `shellparse` now normalises for matching, with a check that fails when the map falls behind the
  classifier.

Requirement coverage went from 5 entries to 8 by re-pointing knowledge the entries already
carried — `hard-delete-permission` is about a permission a hard delete needs and was filed as a
hazard, therefore invisible to closure. No new platform claims were made. Retrieval precision
rose from 85% to 88% as a side effect of removing the over-match; it had been sitting exactly on
its FAIL floor.

**Ask-by-intent shipped the same day.** `torque needs <operation>` is the front door, because
`closure_for` answers from a command and you had to already know what you were going to type.

Free text was rejected as an interface, not as a nicety. Catalogue triggers are regexes written
against CLI text, so a sentence matches nothing and returns an empty set — and that empty set
reads as "requires nothing". Manufacturing exactly that confusion inside the tool built to remove
it would be a joke at the reader's expense. So each of the nine named operations carries a
canonical command as its exemplar, matched by the triggers that already exist rather than by a
second matcher, and `needs_vocabulary_reaches_the_catalogue` fails if one stops reaching an
entry. An unknown operation is refused with the list, never answered with silence.

**Still open here:** `audit-fields-not-writable` carries a requirement no ordinary command
reaches.

### 2. Shadow execution
`Savepoint` → the real DML → the real runtime errors → `Database.rollback`. The trial-and-error
happens inside a transaction that never commits. **Mechanism already proven against a live org:
an insert observed, a genuine `REQUIRED_FIELD_MISSING` captured, residue zero.**

What remains is not the mechanism but the honesty around it: establish by test — not by assertion
— what does *not* roll back (callouts, `@future`/queueable, platform events, change data capture),
how governor limits accumulate inside the shadow transaction, and a hard refusal against
production.

### 3. The completion gate — ledger shipped, browser half still gated
A verified / not-verified ledger, ending in the browser under a **non-admin** profile — admin sees
everything and proves nothing. The same discipline the harness already applies, where a skip is
never allowed to read as a pass, applied to the word *done*.

**`torque done` ships the ledger** (2026-08-04). Six layers: the field exists in the org, FLS is
granted, somebody actually holds the permission set, it renders for a non-admin, the automation
fired, a human agreed. An unobserved layer is NOT VERIFIED, and the denominator does not move when
evidence arrives — layers get answered, never removed, because subtraction is how a partial check
comes to read as a complete one.

**The browser half is not built, on purpose.** It is still gated on the same three questions: can
it render as a non-admin profile at all, what is the real wall-clock cost, and how flaky is it. A
completion gate that costs two minutes gets routed around, which is worse than not having one, and
none of the three has been answered against a real org. So that layer reports BLOCKED with a dated
reason, `--na` is refused on it — a blocker is a fact about the tool, not a judgement about the
change — and a human who did the render by hand records it with `--render-evidence`. Without that
seam the verdict would be NOT DONE for every input ever, which is a ledger with one row and no
information in it.

### 4. Proof-carrying operations — shipped 2026-08-04
The unifying frame: every meaningful operation carries preconditions, predicted impact,
authorization bound to that impact, postconditions, unknowns, and an evidence receipt.

All six existed and none of them met. The catalogue knew what an operation requires,
blast-radius knew what it would set off, approval tokens bound a count ceiling, the completion
ledger knew whether it landed, UNDETERMINED existed in three tools, and attestations recorded
runs — six answers, six commands, and nowhere a person could look and say the operation is
accounted for.

`torque receipt` assembles them. It runs the existing tools rather than reimplementing any,
because a second blast radius would diverge from the first and nothing would compare them;
`receipt_composes_rather_than_reimplements` asserts it stays that way. Verified live: 4/6
INCOMPLETE with no field named, 6/6 PROOF-CARRYING with the field, permission set and the three
kinds of human evidence supplied.

The one rule it is strict about: **a receipt showing five of six must not read as complete.**
The evidence element is the self-referential one — the receipt vouching for itself — and it
withholds itself while any other element is outstanding, naming what is missing rather than just
refusing. Authorization is deliberately reported as verified-elsewhere: an approval token lives
in the trust anchor, which this tool cannot read by design, so it records the ceiling the
approval must name and says the gate checks the binding at write time. Claiming otherwise would
be this tool vouching for a control it has no access to.

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

The current state, measured rather than claimed: 97 checks (77 static, 94 capability, 97 release),
17 mutators, 196 adversarial fixtures, and retrieval measured against an evaluation set written by
someone other than the author of the thing being measured — 94% *matched* recall, 86% *surfaced*
recall, 85% precision over 34 negatives.

Both recall numbers, because they answer different questions and quoting only the first flatters
the tool: MATCHED is whether the entry's triggers fired at all; SURFACED is whether it survived the
two-slot display limit and actually reached the operator. The 8-point gap is a capacity limit
rather than a mis-ranking, and only matched recall currently has a FAIL floor. Precision sits at
85% against a floor of 85% — one more false positive turns the build red, which is the intended
tension and worth stating rather than discovering.
