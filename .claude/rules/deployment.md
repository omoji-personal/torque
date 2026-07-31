# Deployment

Deploy order: objects/fields → permission sets (FLS) → Apex/triggers → flows (draft) →
activation → layouts/pages → profiles (minimal). Formula fields get NO automatic FLS —
deploy a PermissionSet with fieldPermissions alongside every field, even for admins.

- Always `--dry-run` first; deploy success is not outcome success — verify with SOQL after.
- Teardown of probe/test metadata uses `purgeOnDelete` (hard delete; valid on
  non-production orgs) so a run-scoped field does not accumulate in the 15-day deleted
  queue. Delete `PermissionSetAssignment` data records before deleting the PermissionSet.
- Never SOQL/DML in a loop; never hardcode Ids; every Apex class declares its sharing.

ENFORCEMENT: harness-enforced (probe_cycle)
