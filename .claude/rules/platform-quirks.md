# Platform knowledge — consult the catalogue before you assert

The catalogue is `knowledge/salesforce-platform.yml`. It is structured, dated, and every entry
declares how it is known: `verified-live` (re-checked against a real org by `kb_live_claims`),
`documented` (with the Salesforce source named), or `practitioner` (learned by being burned,
and said so). Read it with `grep`, or load the YAML — it is designed to be queried by symptom,
not read front to back.

## When to consult it — objective triggers, not "when unsure"

1. **Before deploying metadata** — `deploy`. Field-level security, deploy order, profile
   semantics, what a Flow retrieve actually returns, what a deleted field does to its own name.
2. **Before any bulk data change** — `data`, `limits`. What a no-op update really costs, what is
   filterable, which permission a hard delete needs, the governor limits that bite at 200 rows.
3. **Before claiming a flow is active, or that automation ran** — `deploy`, `automation`. Order
   of execution, and why an entry condition will not re-fire.
4. **Before writing or reviewing Apex that touches user data** — `security`. `with sharing` is
   not FLS; a formula field can expose the fields it references.
5. **When an API name does not resolve** — `packaging`. The namespace prefix appears in the
   packaging org too, not only in subscriber orgs.
6. **When identical code behaves differently in two orgs, or the CLI rejects valid SOQL** —
   `api`. API version is stored per class and per flow and changes runtime semantics; some
   standard relationships can never be counted; `ALL ROWS` is a CLI flag, not SOQL text here.
7. **Before treating an org as safe because of its type** — `orgs`. Developer Edition is neither
   a sandbox nor production; Full and Partial Copy sandboxes hold real customer data.
8. **When browser or login automation fails** — `auth`. Phishing-resistant MFA, and what
   actually breaks the frontdoor session handoff.
9. **Whenever a result looks green but wrong** — the API reported success and the outcome is
   still wrong. That is the failure this catalogue exists for; search it by symptom first.

If none of these apply, this rule is silent. It is a lookup, not a ritual.

## Why a catalogue rather than model knowledge

Salesforce ships three releases a year, so anything learned from training data decays on a
schedule, and hallucinated API names are the most common cause of a failed deploy. The
catalogue is narrower than the platform and more reliable than recall: it holds the specific
behaviours that produce a *successful* API call with a *wrong* outcome — the failure an agent
cannot detect by reading return codes.

It is deliberately NOT a copy of Salesforce's documentation. Raw docs are neither usable at
this granularity nor redistributable; the useful form is the digested one — symptom, cause,
remedy, and how the claim is known.

## The rule that keeps it honest

An entry marked `verified-live` must name a `verify` function the harness RUNS against a real
org. `kb_integrity` fails the build if a `verified-live` entry has no runnable check, or if a
`documented` entry cites no source. `kb_live_claims` re-runs every live verification and fails
if a platform claim no longer holds — which is exactly what a Salesforce release is most likely
to do to this file.

ENFORCEMENT: harness-enforced (kb_integrity, kb_live_claims)
