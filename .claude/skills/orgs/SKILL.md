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
Adding an org to `local/writable-orgs.json` is an authorization act, so it is gated — by the
file itself, not by a token. The agent's Edit/Write on that path is DENIED by
`prod_write_gate` ("agent modification of protected file … operator-present issuance only").
The agent proposes the entry; **the operator writes it.** There is deliberately no
`approve … allowlist` op: a token the agent can ask for is a weaker control than a file its
Edit/Write tools are denied. (That denial covers the tool surface and Bash write shapes; an
interpreter one-liner that writes the file is the documented subprocess gap — see SECURITY.md.) Only sandbox/developer/scratch verdicts are eligible. Every entry records orgId, username,
verdict, verification time, and disposable flag.
