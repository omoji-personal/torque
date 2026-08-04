# Torque — an AI-agent operations layer for Salesforce

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

## Know what you can change BEFORE you plan

Most of this repo is not agent-writable: `hooks/`, `bin/`, `.claude/`, `harness/checks/`,
`knowledge/`, `local/orgs/`, and protected basenames (`validate.py`, `lib.py`, …) wherever they
live. There is no token for it — `torque approve` mints ORG tokens only. An operator-present
**maintainer window** (`torque approve --maintainer <minutes>`) unlocks source edits, and never
the files the gate reads to decide: the allowlist, the classification cache, the shield, the
read-only manifest.

Harness runs: any `--only <check>` works offline. **With `--target-org`, only the checks declared
`reads_only=True`** — listed in `harness/checks/read-only-checks.json`, currently
`describe_first`, `org_classify`, `kb_live_claims`, `preflight_credentials`,
`detect_probes_run`. Use them to diagnose a live failure yourself. Everything else org-touching,
including `--profile capability|release`, is refused: `probe_cycle` deploys and hard-deletes, so
the harness as a whole is not read-only and never gets treated as if it were.

Ask, do not discover by being refused:

```
python3 scripts/write-surface.py --plan docs/SOME-HANDOFF.md   # before working any plan
python3 scripts/write-surface.py <paths>                       # exit 1 if any are blocked
```

A punch list written from a code survey will name files you cannot touch — that has already cost
one session half its context. Check first, and say up front which items need the operator.

## The rules

`.claude/rules/` auto-loads (byte-budgeted, harness-enforced). Deep detail lives in
`.claude/reference/` — load on demand. The validation contract is
`.claude/rules/validation.md`; nothing ships without its gate.

TORQUE_RULES_TOKEN: TRQ-7f3a9c
