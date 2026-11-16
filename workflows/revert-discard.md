# /revert-discard

Mark a selected snapshot abandoned while keeping its recovery artifacts.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

Use the snapshot and org specified by the user:

```sh
torque recover discard <snapshot-id> --org <org-alias> --workspace <private-path> --client <client-name>
```

`torque recover discard --help` says what changes and what is kept. Verify the updated
status with `/revert-show`. Keep history and exact
recovery artifacts unless deletion is separately requested. If the user merely
asks to view or preview a snapshot, do not mark it abandoned as housekeeping.
