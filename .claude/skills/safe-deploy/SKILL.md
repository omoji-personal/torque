---
name: safe-deploy
description: Deploy metadata to an org through the dry-run → deploy → SOQL/FLS verify → functional check pipeline. Use for any metadata change.
---
# safe-deploy

**What this adds over `sf project deploy start`:** the verify-after and FLS discipline that
deploy-success alone doesn't give you. The pipeline (proven by the harness `probe_cycle`):
1. `--dry-run` first — never deploy blind.
2. Deploy; the write gate confirms the target is allowlisted and non-production.
3. SOQL-verify the component exists; verify FieldPermissions for any field (formula fields
   get NO automatic FLS — deploy a PermissionSet alongside).
4. Functional check where possible; state exactly what was and wasn't verified.
Teardown of anything temporary uses `purgeOnDelete` and deletes PSAs before permsets.
