# How Torque describes itself

One source for the words, so they cannot drift apart across the README, the guide, the repo
description and anywhere else the project is written about.

This exists because they *had* drifted: four different noun phrases for the tool, and a number in
the public repo description that had been wrong for weeks — "128 adversarial tests", which is the
differential-fuzz case count rather than the 196 fixtures. Both numbers are real, which is exactly
how it survived. It was not a lie; it was a number that moved house.

---

## The noun phrase — use this exact form

> **an AI-agent operations layer for Salesforce**

Not "workspace" (it is not a place), not "framework" (too vague), not "guardrails" (that is one
part, and leads with the limitation). Layer is right: it sits between the agent and the org.

## Framing rule — capable, not limited

The tool's value is that it lets you *use* the capability, safely. Copy that leads with what the
agent cannot do sells the constraint instead of the product, and reads as a smaller thing than it
is.

- ✅ "Let an AI agent do real Salesforce work on the orgs that matter."
- ❌ "An AI agent that cannot write to production."

Safety is the *enabler*, stated second. Say what it makes possible, then why you can trust it.

---

## One-liner — GitHub repo description, LinkedIn featured title (≤ 200 chars)

Lead with what it does that nothing else does. Safety is what makes it usable, not what it is.

```
An AI-agent operations layer for Salesforce. It knows the platform, shows you what an operation
will set off before it runs, and verifies every change in the org — on every org you run.
```

## Two-sentence — LinkedIn post opening, handoff opening, README sub-headline

```
Torque is an AI-agent operations layer for Salesforce. It carries platform knowledge that
re-verifies itself against a live org, tells you what a change will actually set off before it
runs — the triggers, the flows, the cascading deletes, the records left orphaned — and proves
every change in the org rather than trusting a return code. It works on every org you run,
production included, because enforcement binds at the tool call instead of in a prompt.
```

## Paragraph — guide lede, README opening

```
Frontier coding agents can already query, deploy, run Apex and move data in Salesforce. What they
lack is judgement about the platform: that a deployed field stays invisible until a permission set
grants it, that a bulk job can report success with thousands of failed records, that deleting
seven accounts can strand twenty-one cases. Torque supplies that. It carries a Salesforce
knowledge base where every claim declares how it is known and the live ones re-verify against a
real org; it can tell you what an operation will set off before it runs; it remembers what each
org you work on actually does; and it ends every operation by asking the org what is true. That it
also makes production a deliberate act rather than an accident is what lets you point it at the
orgs that matter.
```

## Ordering rule for any description, anywhere

State them in this order. The list is the argument.

1. **It knows the platform** — and the knowledge argues with itself. One entry was found wrong
   and corrected by experiment; that experiment now runs on every release.
2. **It shows you the blast radius first** — scope, triggers, flows, validation rules, cascading
   deletes, orphaned children. Nothing else assembles this.
3. **It verifies in the org** — never in a return code.
4. **It gets sharper the longer you use it** — per-org knowledge, keyed so a sandbox refresh
   correctly clears it.
5. **It works on production** — deliberately. Approval can be bound to a verified record count,
   so a token for seven records cannot be spent on seven thousand.

Guardrails appear at position five, as the thing that makes one and four usable on real client
orgs. They are never the headline. A reader who meets the constraint first concludes the tool is
small; a reader who meets the knowledge first concludes it is deep and then learns it is safe.

## What NOT to lead with — test counts

Do not headline the fixture count, the check count, the mutation-test count, or the fuzz-case
count. Anywhere. Not in the repo description, not in the LinkedIn hook, not in the guide's opening.

These are **product-development hygiene, not achievements**. Any serious engineering project has a
test suite; counting it out loud tells a senior engineer that the author thinks having tests is
remarkable, which is the opposite of the signal wanted. "196 adversarial tests" is the kind of
number that impresses a recruiter and quietly costs credibility with the person who actually
evaluates the work.

The distinction worth holding:

- **Product facts** — what the tool knows and does — belong in the headline. "34 platform entries,
  each declaring how it is known, the live ones re-verified against a real org" is a claim about
  the product, and it is unusual.
- **Process facts** — how it was built and tested — belong in the body, for the engineer who digs.
  They should be *evident from the work*, not advertised ahead of it.

The validation discipline here is genuinely stronger than typical — mutation tests that require
each attack to start working when its guard is neutered, a differential fuzzer that checks the
parser against what real bash does, verifiers that must be able to return false. Show that in
section 07 of the guide, where someone evaluating rigour will look for it. Do not put a number on
the cover.

**Current copy that violates this and must change:** the GitHub repo description ("128 adversarial
tests"), and the LinkedIn post hook ("I have 128 tests that break the build if that ever stops
being true") — which is the whole hook of the post, and is the wrong one.

---

## Enforcement

- `claimed_counts` re-derives every count stated in a tracked file from the artifact it describes.
- `public_description_accurate` reads the live repo description and requires every number in it to
  match a metric this repo actually produces. It catches a wrong number; it does not catch a wrong
  label on a right number, which is the defect that prompted it.
