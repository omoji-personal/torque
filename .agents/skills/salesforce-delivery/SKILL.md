---
name: salesforce-delivery
description: Run the work around a Salesforce build, phase by phase. Use it when you take over or assess an org, run discovery or write requirements and user stories, choose and record a solution design, set up or share sandboxes, plan testing or UAT, migrate data, plan a release or deployment, write documentation or training, hand over, support an org or review its health, or estimate and control scope. Gives for each phase what matters most, the artifact it produces and the human sign-off, with checklists, artifact contents, the costly failure modes and the practice that prevents each, and field lessons on verifying before stating, test side effects, other people's work, proportion and writing for clients.
---

# Salesforce delivery

Find your phase in the map, do its few things that matter, produce its artifact, get its
sign-off. State described: Winter '27 (API 68.0), checked 2026-11-14. Building itself is in
skills `salesforce-build` and `salesforce-flow`.

Marks used in this skill: **verify** = not confirmed in a fetched primary source: verify in
the org or the current documentation. field-reported = seen in real client work, usually in
one org. `[HUMAN]` = a person decides or acts; the agent prepares.

## The map

| Phase | What matters most | Produces | Human sign-off | Read |
| --- | --- | --- | --- | --- |
| 1. Take over or assess an org | Resolve which org you are in. Inventory read-only, every figure dated. Change nothing while looking. Take a baseline | Org assessment, baseline record, access and environment register | Client admin fact-checks it; sponsor accepts scope | `references/assess-and-discover.md` |
| 2. Discovery and requirements | Ask why before how. Check every name against the org. Stories with pass or fail criteria. Non-functional needs and the success measure before design | Discovery record, stories with criteria, fit-gap, open-questions log | Stakeholders confirm the write-up; product owner approves the backlog and scope | `references/assess-and-discover.md` |
| 3. Solution design | Native baseline and its effort first. Two options with trade-offs. One decision record per consequential choice. A design the client's admin can run | Solution design, decision records | A second person reviews; the client owner approves schema, access model and estimate | `references/design-and-estimate.md` |
| 4. Environments | Know each org's type and what a refresh destroys. In a shared org, retrieve, compare, deploy only yours. A sandbox can email real people | Environment register, post-refresh runbook | Each refresh; each merge to a protected branch | `references/environments-and-release.md` |
| 5. Build | The two build skills. Enter every sandbox change in the build register the same day | Build register | A second person reviews configuration and code | `references/environments-and-release.md` |
| 6. Testing and UAT | Each criterion: positive, negative, bulk, as each persona. What can a test send outside the org? UAT by people who do the job. Say what was not tested | Test scripts, UAT log, UAT sign-off | The business owner signs UAT and decides go or no-go | `references/testing-and-uat.md` |
| 7. Data migration | Preparation is most of the job. Rehearse at full volume. Bypass list with restore steps. Reconcile with exact counts and control totals | Mapping specification, load runbook, reconciliation report | The data owner approves mapping, rehearsal and the production load | `references/data-migration.md` |
| 8. Release | Validate, then deploy the validated job. Manual-steps register. Keep the current production version as the way back. Deployed is not active. Smoke-test as real users | Deployment runbook, manual-steps register, release notes | The client approver's go or no-go | `references/environments-and-release.md` |
| 9. Documentation, training, hand-over | Write for the reader's job. Dictionary from metadata. The admin guide says how to change things safely. Teach-back | Admin guide, user guides, data dictionary, decision log, hand-off note | The client accepts the hand-over in writing | `references/handover-and-support.md` |
| 10. Support and org health | Evidence before cause. Fix the cause. Error mail goes to someone who reads it. A fix made by hand goes back to source. Review health each quarter | Triage note, health review, debt register | The reporter confirms the fix; the client owner agrees priorities | `references/handover-and-support.md` |
| 11. Estimating and scope | Written assumptions. Count data preparation, testing, deployment and training. A new request is a change request. Compare effort with what the client was told before you polish a plan | Estimate with assumptions, change request log | The client signs the scope baseline; each change is decided before work starts | `references/design-and-estimate.md` |

## Rules that hold in every phase

1. **State org facts from the org they are about, read that day.** Label each with org and
   date. A sandbox is a dated copy. A negative ("nothing sends from here") is unverified
   until checked (field-reported: cards built from a sandbox were wrong about production).
2. **Say what "verified" rests on.** Written, checked, reviewed and accepted are different
   facts. For a check, state as whom, in which org, by what kind of check, and what was not
   covered. A check counts only if its result would differ when the thing is wrong. A green
   deploy or an exit code is not the outcome, and acceptance is never the agent's to set
   (field-reported: 30% of work recorded as verified was contradicted later).
3. **Test on dummy records, never a real person's,** and find out what a test can send
   outside the org before the first record.
4. **In anything other people use, add, do not overwrite.** A sandbox, a flow, a sheet, a
   deck: take the current state, compare, change only what is yours.
5. **Proportion.** The native way and its effort first. No stage, document, safeguard or
   review round that nobody asked for.
6. **Investigate when asked to investigate; carry through what you were asked to do.**
   "Check whether we can fix it" gets findings and a proposal, not changes. A request to do
   work is carried through to a checked result, and its authorization holds until its scope
   changes or it is withdrawn. Ask at an unresolved consequential choice and before an
   outward or irreversible step, with the concrete result prepared.
7. **Client-facing text: vet once and post once.** Restate the request first when its
   purpose or reader is unclear; outline to the reader first when the document is long or
   costly. A review loop that converges is not the reader accepting. A revision after
   review is a change list against the existing document.

Detail and the incidents behind these: `references/working-practice.md`. The 26 failure
modes that cost consultancies most, each with its prevention: `references/failure-modes.md`.

## Browser testing

To test as a user in a browser, sign in through the Salesforce front door with the CLI's
existing authorization: `torque browser login` (see `torque browser login --help`). Do not
type a password, do not ask the consultant to sign in to an org the CLI is authorized for,
and never print or store a sign-in address. Then confirm whose session it is before you act
(`references/testing-and-uat.md`, section 3).

## Record as you go

- At the start: `torque context --workspace . --client NAME` for the client's context and
  recent sessions, and the saved lessons (`torque lesson --help`). Then verify the cheap
  facts that drift: which org, which flow version is active.
- After meaningful work: `torque session add --workspace . --client NAME --summary TEXT
  --status prepared|executed|verified|incomplete`. Keep job ids and evidence files.
- When the consultant corrects you, or a fix took more than one attempt, save it as a lesson
  with its check (`torque lesson --help`), so the next session does not repeat it.
- For the next consultant: `torque handoff --workspace . --client NAME`.
- Find a recipe for a phase with `torque workflows list --workspace .`.

## References

- `references/assess-and-discover.md`: taking over an org, discovery, requirements.
- `references/design-and-estimate.md`: options, decision records, estimates, scope control.
- `references/environments-and-release.md`: sandboxes, shared orgs, the build register,
  release and deployment, deploy and retrieve mechanics.
- `references/testing-and-uat.md`: test design, the checks that would have caught escaped
  defects, as-user testing, side effects, the UAT hand-over pack.
- `references/data-migration.md`: mapping, rehearsal, loading, reconciliation.
- `references/handover-and-support.md`: documentation, training, hand-over, support, org
  health, platform releases.
- `references/failure-modes.md`: costly failure modes and what prevents each.
- `references/working-practice.md`: what "verified" means, org facts, authority, other
  people's work, proportion, documents for clients.

A path that starts with another skill's name, such as
`salesforce-build/references/access-and-sharing.md`, is relative to the skills folder that
holds this skill.

## Sources

- Phase practices, artifacts and sign-offs follow Salesforce Trailhead and Well-Architected
  guidance cited in each reference file; start at https://architect.salesforce.com/docs/architect/well-architected/guide/operational-excellence.html
- Front-door sign-in (single-use address from the CLI's authorization): https://github.com/forcedotcom/cli/blob/main/releasenotes/README.md
- field-reported: lessons from real client work with Torque and its predecessor (one
  consultant, about a dozen clients, mostly sandboxes), including an after-action review of
  166 rework and defect episodes and a classification of 273 failed Salesforce CLI calls.
  Not re-tested for this release.
