---
description: "Scan a replay script for credential patterns and report masked findings."
---

# /qa-sanitize-replay

Scan a replay script for credential patterns and report masked findings.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

Use the actual path supplied by the user:

```sh
torque browser sanitize-replay <script-path>
```

`torque browser sanitize-replay --help` says what the scan reads, what it cannot see and what
each exit code means. Report detected category, file and line; never echo the matched secret.
Distinguish a missing or unreadable file from a clean scan, and do not present a clean scan as a
full secret audit or as proof that a replay is safe to publish.

Inspect fixture inputs, screenshots, embedded URLs and any generated outputs
included in the requested review. If removal is requested, replace embedded credentials
with runtime authentication and verify the sanitized script remains meaningful.
Do not claim a credential was revoked merely because its literal text was removed.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
