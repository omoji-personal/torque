---
name: salesforce-architecture-review
description: Review a Salesforce data model, automation design, integration, access model or cross-component solution against business outcomes and maintainability. Use it when asked to review, assess or sign off a design or an existing org, to compare design options, to judge whether a build is over-engineered or risky, or before a consequential design decision is recorded. Gives a review method, the five Well-Architected pillars as a lens, a checklist of forty rules by phase, and a catalogue of forty anti-patterns with how to detect each.
---

# Salesforce architecture review

A review reads and reports. It does not change the org or prove that an implementation
works. State described: Winter '27 (API 68.0), checked 2026-11-14. To build, use skills
`salesforce-build` and `salesforce-flow`; the decision tables live there.

Marks: **verify** = not confirmed in a fetched primary source: verify in the org or the
current documentation. "Lead" = a query or element name written from the research, not run
for this release: confirm names with describe, and treat an error as "not checked".

## Method

1. **Fix the question.** What outcome must the design serve, for whom, and what decision
   does the review feed? Write it in two lines and get a yes.
2. **Gather evidence, read-only.** The design document or the metadata; the org's shape
   (product family and installed packages, automation already on the objects in scope, the
   sharing model, data volumes); who will maintain it. Name the org and the date on each
   fact.
3. **Separate four things** as you read: what a managed package does, what the client
   customised, what the client decided, and what is assumed without evidence.
4. **Trace one real transaction end to end**: the page a user opens, the access that lets
   them, the automation that fires, the data written, the outside systems reached, what
   happens on failure and on the second attempt.
5. **Check it against the anti-pattern catalogue** (`references/anti-patterns.md`) and the
   rules for its phase (`references/review-checklist.md`). Apply only what the change needs.
6. **Weigh trade-offs with the five pillars** (`references/decision-methods.md`). Do not
   maximise every pillar: say which trade-off was made and whether it fits the size of the
   org.
7. **Look for the simpler design.** Would a standard feature, an installed package, or
   configuration do the job? Is the effort in line with what the client was told? A simpler
   native option or an over-budget plan is a top finding, not a footnote.
8. **Report** (below), and state what still needs confirmation.

## The five pillars, as questions

| Pillar | Ask |
| --- | --- |
| Trust | Is access least privilege by default? One identity per integration? No secrets in code or configuration? Data classified at field level? |
| Reliability | Does it hold at 200 records and at the real volume? What happens when a step fails, and on a retry? Can the data be restored? |
| Operational Excellence | Can it be deployed, validated and rolled back from source? Who hears about a failure? Is it documented where an admin will look? |
| Resource and Cost Optimization | Was what the org already owns weighed first? Who maintains it, at what cost over three to five years? Any unused automation left behind? |
| Fairness | Can everyone use the pages (labels, keyboard, contrast)? Does a rule or a required field shut out people who lack the data? Is a human in the loop for high-stakes decisions? |

Source: [WA-FW]. The older Trusted, Easy, Adaptable structure was replaced on 2026-09-14
and its page addresses no longer resolve [WA-BLOG]: treat any guidance that cites it as
stale.

## What to report

| Field | Content |
| --- | --- |
| Severity | Blocks the decision (data loss, exposure, cannot work at volume, cannot be undone) / Fix before production / Improve when next touched |
| Finding | One sentence, on one component or decision |
| Evidence | The file and line, the query and its result, or the document section; org and date |
| Why it matters | The outcome or the maintenance cost it affects, in the client's terms |
| Recommendation | The smallest maintainable change; the trade-off only when it changes the decision |
| Confidence | Read in source, observed in the org, or assumed |

- Report one defect once. Treat untested behaviour as a gap, not a defect.
- Do not assert a platform limit from memory: cite it or mark it to check.
- Say what you checked and what you did not.
- A design review is not evidence that the implementation works. When implementation is
  also requested, continue with the agreed design and the build skills.

## Stop and ask a person when

A change trades one pillar against another (speed against access control, cost against
recovery); a target such as availability, recovery time, retention or a compliance figure
would have to be invented; the framework and the client's own convention disagree; the
design touches anything on the stop-and-ask lists in `references/review-checklist.md`.

## References

- `references/review-checklist.md`: forty rules by phase, each with where the detail lives.
- `references/anti-patterns.md`: forty smells, how to detect each, the better pattern.
- `references/decision-methods.md`: pillars and tensions, proportion, options and decision
  records, org strategy, operations, what changed recently.

A path that starts with another skill's name, such as
`salesforce-build/references/choosing-the-tool.md`, is relative to the skills folder that
holds this skill.

## Sources

- [WA-FW] https://architect.salesforce.com/docs/architect/well-architected/guide/framework.html
- [WA-BLOG] https://www.salesforce.com/blog/new-well-architected-framework/
- Field-reported practice (trace one transaction, simpler option as a top finding): lessons
  from real client work with Torque, not re-tested for this release.
