---
name: mass-update
description: Update many records safely — preview the exact ID set and diff first, capture before-values, then apply only real changes. Use for any multi-record data change.
---
# mass-update

**What this adds over `sf data update`:** a field-diff dry-run and an undo trail, so a bulk
change can't silently clobber. Flow:
1. Query current values; build the exact ID set and a per-field diff; show counts.
2. Capture before-values (the undo data) to `local/clients/<client>/<orgid>/`.
3. Capture a `SystemModstamp` precondition per record (check-then-act window: documented,
   single-operator assumption stated).
4. Apply only records whose value actually changes; journal each SaveResult.
5. On undo, refuse any record whose current value no longer matches what this run wrote.
An unbounded (WHERE-less) update is blocked by the destructive gate — scope it or approve it.
