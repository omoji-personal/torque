# Working discipline

- **Session logs carry before-values.** After any org change, append to
  `local/clients/<client>/<orgid>/session-log.md`: what changed, before→after, records by
  Id, what was verified, what needs human UAT. Capture before-values BEFORE the change.
- **Privacy.** No PII, no credentials, no raw query rows in logs. Redact via `lib.redact`.
  Log the minimum needed for undo and future context.
- **QA honesty.** State exactly which layers ran: metadata validation, static analysis,
  tests, functional, UI, human UAT still needed. SKIP is not green. Never claim a check
  that didn't run.
- **Test records:** create flagged, delete by Id only.
- **Mass updates:** diff-first; the check-then-act window (query modstamp → update) is a
  single-operator assumption — stated, not hidden.

ENFORCEMENT: harness-enforced (session_log_integrity)
