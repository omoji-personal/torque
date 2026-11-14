# Evidence and QA

- Distinguish observed facts, supplied assertions, hypotheses, unknowns, untested
  outcomes and not-applicable checks. Dates, scope and actual user identity matter.
- Inspect the exact changed components and intended behavior. A successful API call,
  deployment status or generated report does not prove the whole business outcome.
- "Verified" names its basis: as whom (`--as-user`), in which org (`--in-org`), by what kind
  of check (`--check read-back|scripted|rendered|live|reviewed`) and what it did not cover
  (`--not-covered`) on the session record. A read-back of what you wrote is not a check of
  behaviour. `--status accepted` only when someone else said yes, with `--accepted-by`.
- Permission configuration, assignment to a user, effective access, record access
  and the rendered interface are separate checks. A sysadmin query is not end-user UAT.
  A FieldPermissions row or a permission set entry is a grant, not a user's access:
  test as the user before saying someone can see or edit something.
- For Flows, identify the actual active definition/version separately from a recent
  retrieval. Verify relevant entry, negative, fault and bulk paths as the change requires.
- Use relevant QA surfaces and report their limitations. Optional legacy QA skip
  tokens are compatibility helpers, never a condition for ordinary execution.
- A browser journey can mutate data. Resolve its actual user and intended side
  effects, use one driver per shared session, and verify any cleanup that was performed.
- Concurrent independent reads are useful. Sequence only work that actually shares
  mutation state, an authenticated browser session or a dependent deployment path.
- Record synthetic tests as synthetic, manual assertions as supplied, and live checks
  as live. No numeric risk score or test count substitutes for these distinctions.
