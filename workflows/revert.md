# /revert

Restore a supported captured operation and verify the resulting state.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

Use `/revert-preview` for the requested snapshot against the intended current org.
Review exact scope, recovery files and intervening drift; use existing authorization
for the requested reversal. Then use the supported delegate:

```sh
torque revert revert exec <snapshot-id> --org <org-alias> --workspace <private-path> --client <client-name>
```

A refusal due to missing pre-state or unsupported reversal is a real capability
limit, not an invitation to invent a restore. Diagnose it and use a scoped manual
inverse where actually possible. Never append `--force` automatically: inspect
its installed semantics and the concrete consequences before considering it within
the user's scope. No Torque approval/allowlist/token step is added.

Track the restoration's own job/results and any parent snapshot chain. Verify exact
metadata/record readback and the user's intended outcome. Restore-related metadata
success does not undo all downstream data or external effects. Save completed,
partial, failed and unrecovered parts explicitly in the client session.
