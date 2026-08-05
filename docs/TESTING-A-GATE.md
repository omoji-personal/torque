# Testing a gate you are about to put in someone's way

Four methods, none of them Salesforce-specific, all of them learned by getting them wrong first.
They apply to any policy engine that sits between an actor and a system it can damage: a WAF, a
CI admission controller, a permissions layer, a PreToolUse hook.

They are written down because the failure they address is not "the gate is wrong." It is worse
than that and much harder to see: **the gate is right, the tests agree, and the thing is unusable.**

---

## 1. Proving a control refuses is not proving it accepts

A gate that denies everything passes every deny fixture.

That sentence is obvious and it still cost this project four separate defects in one day:

- A check that verified the operator-presence control **refused** without an operator, and never
  that it **accepted** with one.
- An initialisation command in the same state — refusal proven, success never observed.
- 193 adversarial fixtures with 44 `allow` cases among them, whose entire query vocabulary was
  four copies of the simplest possible query, with no WHERE clause, no `IN` list, no subquery.
  They missed a parser defect that denied **71% of six months of real commands**.
- A dispatcher shipped specifically to close a gap — every denial message told the operator to run
  a command that did not exist — and nothing put it on `PATH`, so the command every denial named
  returned `command not found`.

The fourth is the one that makes this a rule rather than a testing tip. It is not a testing
mistake at all. It is the same shape wearing different clothes: **something verified to be
present, and never verified to be reachable.**

Corollaries, each also learned by being bitten:

- **An examination of nothing reports N/A, never PASS.** An empty corpus passes every assertion.
- **Agreement at the wrong verdict is not agreement.** Two code paths that both refuse a legal
  command agree perfectly and are both wrong. One of our checks reported exactly that as a pass
  for as long as the false positive existed.
- **A mutation must exercise the guard it names.** If a realistic regression takes two changes and
  either alone is inert, mutate both — otherwise the mutation reports a vacuous pass. Ours took
  three attempts before it fired.

---

## 2. Backtest the gate before you deploy it

You cannot run a new gate forward six months. You can run it backwards.

Take a corpus of commands that were **really issued, really executed, and really worked** — an
agent's tool-call history, a shell history, a CI log — and replay it through the classifier
offline. Every refusal is either a defect or a day-one obstacle. Nothing touches the live system.

The first run here refused **80.1% of 1,193 real commands**, and **854 of those 955 denials were a
single defect**: a shell parser that scanned raw text for grouping characters without tracking
quotes, so a parenthesis inside a quoted query string read as shell grouping. `WHERE Id IN
('a','b')` — denied. A relationship subquery, the most common idiom in the language after a plain
select — denied.

193 fixtures had not found it. One replay found it in a single pass, because a corpus of real
commands has a distribution that hand-written allow cases do not.

Two things make the result honest rather than flattering:

- **It measures friction, not correctness.** A denial may be the gate correctly catching something
  that deserved a human. The classifier says what would happen, never whether it was right.
- **It is an upper bound.** Every command in the corpus came from an actor with no gate installed.
  An actor working *under* the gate adapts — names its target, requests approval, rephrases. So
  the number is "how much would the unconstrained way of working collide," not "how much will
  break."

---

## 3. Get a second opinion without ingesting anyone's data

A backtest is only as good as its corpus, and one operator's history is one operator's habits. The
obvious next step — ask another practitioner for their command history — is the step nobody takes,
because a command history is full of customer names, record identifiers and query text.

So invert it. Ship the check; let the corpus stay where it is:

```
CORPUS=<their history> validate --only <the must-allow check>
```

Counts and refusal codes come back. Commands never leave their machine. The check reports **N/A
when no corpus is supplied** — not PASS, per §1 — and treats an unreadable corpus as a failure
rather than an empty one, because a corpus that silently shrinks reports a rate for a sample
nobody chose.

The assertion should be an **invariant, not a threshold**: *no command may be refused for a reason
that is not policy.* A threshold gets renegotiated; an invariant gets fixed.

---

## 4. Separate "I judged this unsafe" from "I could not read this"

This is the one that changes the product rather than the test suite.

After the parser fix, 706 refusals remained. Split by cause:

| cause | count |
|---|---|
| could not statically resolve the command | **686 (97.2%)** |
| actual policy decision | 20 |

The classifier was answering two different questions and reporting both the same way: *is this
authorized*, and *can I parse this*. Failing closed on the second is correct for a layer that sees
a command string before the shell has touched it — it genuinely cannot know what a substitution
will become. What is not correct is that the user cannot tell the two apart. It reads as "the tool
said no," and it lands hardest on whoever composes shell most fluently, which is the person the
tool is for.

Two changes follow, and both are general:

- **Decide later, where the ambiguity is gone.** An exec-time layer sees the arguments the kernel
  is about to run, after every expansion has happened. There is no text left to reason about. When
  that layer is verifiably installed, the earlier layer can defer to it instead of guessing — and
  deferring is *more* accurate than denying, because one of them sees the truth and the other sees
  a guess. Refusals fell from 706 to 22.
- **A refusal must name its own remedy.** 686 messages said the equivalent of "not statically
  authorizable" and offered no route forward. A user who hits that twice concludes the tool is
  broken, and is not wrong to. Say that the limit is a parsing one, that the operation was never
  judged unsafe, and print the command that fixes it.

Of the 22 that remained, four concerned the operation itself. The other eighteen were file-path
guards firing on the non-operational half of compound commands.

---

## 5. What none of this establishes

All four methods together prove that a gate decides correctly and does not obstruct a recorded
past. **They say nothing about whether anyone can work through it**, because a replayed command
never adapts, never rephrases, never asks for approval, and never gives up and does the job by
hand somewhere the gate cannot see.

That last one is the trap. When a gate blocks and a deadline is close, work does not stop — it
moves. It moves to the console, the GUI, the unhooked terminal. The work completes, so any measure
of "did the work get done" reports success, while the dangerous operation has relocated to a
channel with no audit trail at all.

Its sibling: an actor that adapts by *not attempting* things. Refusals fall to zero, every metric
goes green, and the cost has moved into silence where no instrument is pointed.

Neither shows up in a log, because in both cases nothing happened. They have to be counted by
hand, by the person doing the work, and the counting has to cost seconds — otherwise you get a
verdict with no evidence under it, which is worse than no verdict.
