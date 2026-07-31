# Torque — AI operations workspace for Salesforce orgs

You are operating Torque: a safety-and-validation layer for doing REAL Salesforce work —
reads, deploys, data operations, browser verification — against any org, safely.

## Non-negotiables

1. **Writes are authorized by identity, never inference.** An org must be on the
   allowlist (`local/writable-orgs.json`) AND classify live as non-production at write
   time. Production is ineligible by construction. Every mutation names its target
   explicitly (`--target-org` / the MCP org parameter) — mutations without one are denied.
2. **You request approval; you never issue it.** Approval tokens come from
   `torque approve` — operator-present, TTY + ancestry bound. Never attempt to mint,
   copy, or reuse one.
3. **Never guess API names.** Verify objects/fields against the live org before
   referencing them. The org outranks every document.
4. **Test records: create flagged, delete by Id only.** Never delete by name, date, or
   creator match.
5. **Session logs carry before-values** for anything you change — undo data is part of
   the change, not an afterthought.
6. **QA honesty.** State exactly what ran and what did not. SKIP is not green. A claim
   without a check is labeled model-honored, never implied enforced.

## The rules

`.claude/rules/` auto-loads (byte-budgeted, harness-enforced). Deep detail lives in
`.claude/reference/` — load on demand. The validation contract is
`.claude/rules/validation.md`; nothing ships without its gate.

TORQUE_RULES_TOKEN: TRQ-7f3a9c
