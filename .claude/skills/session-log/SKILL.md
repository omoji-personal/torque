---
name: session-log
description: Append a structured session entry with before/after values for undo. Use after any org change (deploy, data op, config).
---
# session-log

**What this adds:** durable, undo-capable history per org. Appends to
`local/clients/<client>/<orgid>/session-log.md`: date, what changed, before→after values,
records touched (by Id), what was verified, what still needs human UAT. Redacts secrets
(via `lib.redact`); stores no PII, no raw query rows — only what undo and future context
need. Before-values are captured BEFORE the change, never reconstructed after.
