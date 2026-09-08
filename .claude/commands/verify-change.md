---
description: "Verify the actual deployed or configured outcome and record remaining uncertainty."
---

# /verify-change

Verify the actual deployed or configured outcome and record remaining uncertainty.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Use the business criteria and intended actor to choose checks. Existing test or
pipeline evidence can be reused when its exact target, source, scope and freshness
fit this change. Add missing checks instead of repeating an entire suite merely
to produce Torque output. When using a change record, retain actual criterion
results, actor, target, source revision/date and private evidence with `change check`;
those checks remain operator-reported. Keep exact technical deployment observations
separate with `change verify-deploy`. Neither one substitutes for the other.

Identify the exact org, change/diff, deployment job and desired user behavior. Query
the specific job rather than “the latest deployment.” Inspect component-level and
row-level failures; successful transport or completed processing is not full success.

- Fields: verify metadata and relevant CRUD/FLS grants, assignment to the intended
  user, other permission sources including groups/muting, record access and UI.
  A missing grant in one permission set is a narrow fact, not full effective access.
- Flows: inspect the definition's actual active version separately from retrieved
  source; test entry/negative/fault paths and relevant automation interactions.
- Pages: inspect metadata and assignments, then the intended user's rendered path
  where needed. API retrieval does not substitute for a user-session observation.
- Settings/data: read back the exact scope/keys, account for partial failures and
  check relevant side effects. Do not prove migration success by counts alone.

Use the advisory and QA delegates where useful. Keep machine observations, supplied
human/browser assertions, inference, declared not-applicable, unknown and untested
states distinct. Report unavailable permissions, missing sources and stale evidence.

If the user asked for a working outcome, correct newly found defects within scope
and recheck. Save a concise session entry and artifact links. State exactly what
was verified and what another user or tester still needs to do.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
