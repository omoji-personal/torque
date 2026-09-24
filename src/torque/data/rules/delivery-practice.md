# Carry the requested work through delivery

- Use the user's actual authorization and normal Salesforce CLI, MCP and browser
  tools. Torque adds no mandatory grant, allowlist, maintainer window or write gateway.
- Complete authorized implementation and fixes; do not stop at advice, a checklist,
  or a report when the request is for a working result. Ask only when scope or a
  consequential destination remains unresolved.
- Resolve the actual org behind an alias before consequential operations. Reuse
  current checks rather than repeating a full preflight before each tool call.
- Retrieve relevant current metadata before changing existing components. Preserve
  unrelated work, retain the baseline and edit a scoped proposal/diff.
- Choose validation and tests that address the changed behavior. Diagnose failed
  validation and inspect real component/row failures rather than ignoring them or
  adding an unrelated policy ceremony.
- Keep recovery evidence appropriate to the operation. Exact private pre-values may
  be needed; a redacted summary or record count is not a reversible backup.
- Track asynchronous work by its exact job ID, then inspect and verify the result.
  Do not claim success from a queued job or substitute whichever job ran most recently.

Ordinary operational errors and real capability limitations still need resolution.
They are not reasons to restore the retired Torque enforcement system.

In a workspace with `approval: required`, `production-approval.md` overrides this rule for production.
