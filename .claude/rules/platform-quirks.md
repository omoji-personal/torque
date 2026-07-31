# Platform quirks (assertions; dated detail in reference/platform-quirks-detail.md)

- `sf apex run` HTML-escapes `|` — use a different delimiter for Apex→shell payloads.
- History objects: `NewValue`/`OldValue` cannot be filtered in SOQL — pull and filter client-side.
- A bare `COUNT()` breaks under an auto-appended `LIMIT` — group or scope it.
- `FlowDefinitionView` can return 0 rows on subscriber orgs even when flows are Active —
  retrieve metadata and grep `<status>` instead.
- Custom-field delete without `purgeOnDelete` sits 15 days in the deleted-fields queue and
  keeps counting against the object's field limit.

ENFORCEMENT: model-honored (observable: each entry names a reproduction; no single
deterministic check spans the catalog — the probe_cycle check exercises the field-delete
and purgeOnDelete entries specifically)
