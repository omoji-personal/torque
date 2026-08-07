# Working discipline

- **Session logs carry before-values.** After any org change, append to
  `local/clients/<client>/<orgid>/session-log.md`: what changed, before→after, records by
  Id, what was verified, what needs human UAT. Capture before-values BEFORE the change.
- **Privacy.** No PII, no credentials, no raw query rows in logs. Redact via `lib.redact`.
  Log the minimum needed for undo and future context.
- **QA honesty.** State exactly which layers ran: metadata validation, static analysis,
  tests, functional, UI, human UAT still needed. SKIP is not green. Never claim a check
  that didn't run.
- **Test records:** create flagged, delete by Id only — **and put them on a CUSTOM object.**
  A by-Id delete of a protected sObject (`Account`, `Contact`, `Opportunity`) is operator-gated
  (`protected-record-delete`, added 2026-08-05), so the mandated teardown spelling is exactly the
  one an agent cannot perform on those three. Measured 2026-08-06: by-Id delete of `Account` is
  refused on both the argv and MCP surfaces; the same delete of a custom object passes on both.
  The gate is right — it cannot know a record was agent-created — so the discipline moves instead:
  fixture data belongs on a custom object, or operator teardown is planned into the run. Creating
  on a protected object is still ungated, so this is easy to get wrong in the direction that
  leaves residue nobody can clear.
- **Mass updates:** diff-first; the check-then-act window (query modstamp → update) is a
  single-operator assumption — stated, not hidden.

ENFORCEMENT: harness-enforced (session_log_integrity)
