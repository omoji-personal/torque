# Platform quirks (assertions; dated detail in reference/platform-quirks-detail.md)

- `sf apex run` HTML-escapes `|` — use a different delimiter for Apex→shell payloads.
- History objects: `NewValue`/`OldValue` cannot be filtered in SOQL — pull and filter client-side.
- A bare `COUNT()` breaks under an auto-appended `LIMIT` — group or scope it.
- `FlowDefinitionView` is a STANDARD-API object — querying it via the Tooling API errors, which
  some clients surface as 0 rows. Read `FlowDefinition.ActiveVersionId` (Tooling) or
  `FlowDefinitionView.IsActive` (standard). Do NOT grep `<status>` from a retrieve: since API v44
  a retrieve returns only the latest version, so an active v3 reads Draft once someone clones it.
- A deleted custom field sits 15 days in the deleted-fields queue and keeps counting against the
  object's field limit. Salesforce renames it with a `_del` suffix, freeing the original API name
  once; a second delete of the same name cannot append `_del` and the name is then blocked.
  `purgeOnDelete` makes a component ELIGIBLE for deletion — it does not erase it, so a `_del`
  tombstone is expected after teardown and residue checks must look for it.

ENFORCEMENT: model-honored (observable: each entry names a reproduction; no single
deterministic check spans the catalog — the probe_cycle check exercises the field-delete
and purgeOnDelete entries specifically)
