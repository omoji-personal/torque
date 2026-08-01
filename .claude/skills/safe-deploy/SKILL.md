---
name: safe-deploy
description: Deploy metadata to an org through the dry-run → deploy → SOQL/FLS verify → functional check pipeline. Use for any metadata change.
---
# safe-deploy

**What this adds over `sf project deploy start`:** the verify-after and FLS discipline that
deploy-success alone doesn't give you. The pipeline (proven by the harness `probe_cycle`):
1. **Describe first.** Confirm every referenced object and field against the LIVE target org
   before writing metadata that names them. A hallucinated API name is the most common cause
   of a failed deploy and the cheapest to catch. (`live-verification.md`; the `describe_first`
   harness check proves it against a real org.)
2. `--dry-run` next — never deploy blind.
3. Deploy; the write gate confirms the target is allowlisted and non-production.
4. SOQL-verify the component exists; verify FieldPermissions for any field (formula fields
   get NO automatic FLS — deploy a PermissionSet alongside).
5. Functional check where possible; state exactly what was and wasn't verified.
Teardown of anything temporary uses `purgeOnDelete` and deletes PSAs before permsets.
