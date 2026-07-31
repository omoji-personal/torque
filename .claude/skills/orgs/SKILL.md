---
name: orgs
description: Connect a Salesforce org, classify it, and manage the write allowlist with per-org operator confirmation. Use when connecting a new org, checking org status/health, or authorizing writes.
---
# orgs

**What this adds over `sf org login`:** classification + allowlist governance. Connecting
an org is `sf org login web`; deciding whether Torque may WRITE to it is the part that
needs discipline.

## Connect
`sf org login web --alias <alias>` (operator does this; agent never enters credentials).

## Classify & status
`python3 hooks/lib_cli.py classify <alias>` → developer/sandbox/scratch/production.
Reads a live `Organization` query, username-keyed cache. Production and unverifiable orgs
are reported as write-ineligible.

## Allowlist a writable org (operator-present)
Adding an org to `local/writable-orgs.json` is an authorization act, so it is gated: the
agent proposes the entry; the operator confirms via `torque approve <orgId> allowlist`.
Only sandbox/developer/scratch verdicts are eligible. Every entry records orgId, username,
verdict, verification time, and disposable flag.
