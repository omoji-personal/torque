# Deep-training Torque on Salesforce — the systematic plan

## What "training" can and cannot mean here

Nothing in this plan changes a model's weights. What it changes is **what reaches the model at
the moment it decides something**, and whether that material is true. Torque already has the
delivery mechanism — the gates parse every command, so the relevant entry prints as the operation
goes past, including on a refusal. What it lacks is a corpus behind that mechanism worth
delivering, and a way to know what is missing.

So the goal is stated precisely:

> Raise the share of Salesforce operations where Torque has something **true, specific and
> verifiable** to say at the moment of the operation — and be able to state what that share is.

The last clause is the hard part. 39 entries is not a coverage claim, because there is no
denominator. This plan builds one.

---

## Stage 0 — The corpus (first-party, not a mirror)

Salesforce publishes a machine-readable documentation index at
`developer.salesforce.com/docs/llms.txt`: **44 documentation sets**, each a plain-text
`llms-<set>.txt`. First-party, fetchable, and stable enough to diff.

This matters more than it sounds. `help.salesforce.com` serves an LWR JavaScript shell — fetch it
and you get a loading spinner, which is why doc-grounded work on this project kept falling back to
recall. The `llms.txt` corpus is the way in, and being first-party it outranks any third-party
mirror in the precedence chain.

**Measured constraint, found while building this.** The corpus is two levels: each
`llms-<set>.txt` is an index of `.md` leaf pages, and the leaves are the actual content. The
indexes fetch fine unattended; the leaves return **HTTP 403 to a scripted client** — tested with
both a bare and a browser User-Agent. So Tier A (indexes) is a pipeline and Tier B (leaves) is
assisted, fetched in-session by a browser-grade client. Cached now: 9 sets, 125,860 B, enumerating
**919 leaf pages** — which is the point, because the queue of what has not been read is now
visible instead of unknown.

**Deliverable:** `scripts/salesforce-docs/ingest.py` — fetch by set, cache under
`knowledge/salesforce-docs/<set>.txt`, record source URL, fetch date and content hash in
`_index.json`. Re-runnable, diffable, and the hash is what tells us a doc changed under us.

**Priority order**, by how much of Torque's adjudicated surface each set touches:

| Priority | Set | Why |
|---|---|---|
| 1 | `platform` | Apex, SOQL, governor limits, Metadata API, flows — most of the surface |
| 2 | `salesforcedx` | the CLI Torque parses and drives |
| 3 | `security` | sharing, FLS, permission sets — the authorization model |
| 4 | `metadata-coverage` | what is deployable at all, per API version |
| 5 | `connect-rest`, `event-bus`, `graphql-api` | the other write paths into an org |
| 6 | `code-analyzer`, `dataloader` | tools an agent invokes |
| — | Marketing/Commerce/Industries/Mobile | out of scope; Torque is an org-operations layer |

---

## Stage 1 — The coverage map (the denominator)

A catalogue without a denominator can only report its own size. This builds the grid:

**Rows: the operation classes Torque actually adjudicates.** Derived from the gates themselves —
every `sf` topic/verb pair the parser classifies, plus the MCP write shapes. That list is already
in the code; it has never been used as a coverage yardstick.

**Columns: failure modes.** Silent success, partial success, permission-shaped, ordering-shaped,
limit-shaped, irreversible.

Each cell is either covered by an entry, known-uncovered (in the gap log), or not applicable.
The report is a percentage with a stated denominator, and the honest form of the sentence becomes
*"Torque has something to say about N of M operation × failure-mode cells"* rather than
*"39 entries"*.

**Deliverable:** `harness/checks/check_coverage.py` producing that grid, and failing when a
newly-parsed operation class has no cell at all — so the map cannot silently fall behind the
parser.

---

## Stage 2 — Extraction, with citation discipline

For each priority set, work the corpus into candidate entries. The rules that already govern the
catalogue apply unchanged, and they are what keep this from becoming a scraped listicle:

- Every entry declares **how it is known** — `verified-live`, `documented` (with the page cited),
  or `practitioner`.
- An entry earns its place by describing a way an operation **silently produces a wrong answer**.
  Reference material that a describe call would answer does not belong here.
- Claims get established **before** they are written down. Two of the three catalogue errors found
  by review were written from recall and cited afterwards.

**Target:** the priority-1..4 sets worked through, entry count roughly tripled, and — more
important — the `documented` tier carrying real page URLs rather than guide names.

---

## Stage 3 — Push entries down the confidence ladder

`documented` is weaker than `verified-live`, and the ratio is the honest measure of the
catalogue's quality. Every entry whose mechanism is observable in an org gets a verifier that
**can return False**, registered and proven falsifiable by `verifiers_can_fail`.

Current: 10 of 39 verified-live. Target: every entry whose claim is observable, with the
remainder explicitly classified as not-observable and why.

`EntityParticle` and `FieldDefinition` turned out to expose far more field metadata to SOQL than
the catalogue was using — that is the seam where `documented` entries become `verified-live` ones
cheaply.

---

## Stage 4 — The gap log (the inverse of coverage)

Record every lookup that found nothing. A catalogue that only records what it knows cannot tell
you what it does not, and "no entry fired" is indistinguishable from "nothing to say" without it.

**Deliverable:** `knowledge/_gap-log.md`, appended by the gate when an adjudicated operation
matches no entry, and reviewed as the queue for Stage 2's next pass.

---

## Stage 5 — Retrieval quality: does the right thing actually fire?

**This is the stage that decides whether any of the rest matters.** An entry that never reaches
the decision is worth nothing, and an entry that fires on everything is worse than nothing,
because it teaches the operator to skim past notes.

Every entry carries a `triggers:` regex list. Nothing has ever measured them as a set. So:

- **Recall** — for a corpus of real commands with known-correct expected entries, how often does
  the right entry fire?
- **Precision** — how often does an entry fire on an operation it has nothing to do with?

`kb_injection` today checks three hand-picked operations. Stage 5 replaces it with a scored
fixture corpus and a floor that fails the build. Recall and precision are the two numbers that
should appear in the README instead of the entry count.

---

## Order of work, and why

Stage 0 and 1 first, because they are what turn this from a pile into a measurable thing.
Stage 5 immediately after — before bulk extraction — because tripling a corpus whose retrieval
is unmeasured just triples the unmeasured part. Stages 2 and 3 then run as a loop against the
coverage map and the gap log, which is the point at which "deep training" becomes routine rather
than a project.

## What this will not fix

Coverage of the platform is not the same as judgement about a specific org. Everything here makes
Torque better at what is true of Salesforce; what is true of *your* org stays the job of the live
verification path and the per-org store. The two are deliberately separate, and the second one
outranks the first whenever they disagree.
