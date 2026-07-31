# Org safety

**Production is ineligible for writes by construction.** Torque exposes no configuration
path that authorizes a write to a production-classified org. Residual shell-level paths
are bounded by Layer 0 (credentials) per the threat model.

## Layer 0 — credentials
Connect production read-only / least-privilege. If a firm cannot provide that, the org is
not connected, or connected under a documented client-acknowledged exception recorded in
`local/clients/<client>/<orgid>/`. sf CLI auth is the only credential path; Torque stores
no secrets. `local/` holds org Ids, notes, before-values only.

## Layer 1 — authorization by identity
A write is allowed only when the target org is on `local/writable-orgs.json` AND classifies
non-production **at write time** (a live `Organization` query, username-keyed cache
invalidated on orgId drift). Membership is necessary, never sufficient. Only
`sandbox` / `developer` / `scratch` verdicts are eligible; `scratch` needs local dev-hub
evidence, else a trial-shaped org classifies `production`.

## Layer 2 — the gates (deterministic, fail-closed)

ENFORCEMENT: hook-enforced (prod_write_gate)
ENFORCEMENT: hook-enforced (destructive_data_gate)

Every mutation names its target explicitly (`--target-org`, or the MCP org parameter) —
mutations without one are denied. Destructive operations (bulk/hard delete, WHERE-less
update, destructive metadata, anonymous Apex) require an operator-present approval token
from `torque approve` — **you request approval, you never mint it.** Anonymous Apex needs
`--file` (stdin cannot be digest-bound). Protected sObjects are shielded on every org.
Authorization artifacts (the allowlist, protected-objects) are not agent-editable.

## Workspace scope
Hooks load from the active workspace. A session started elsewhere loads no gates — that
boundary is stated, and an optional user-level installer (P3) registers the gates from a
recorded TORQUE_HOME for cross-workspace protection.

## Test records
Create flagged; delete by Id only — never by name, date, or creator match.
