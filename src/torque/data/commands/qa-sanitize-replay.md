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

It scans the text as written and after up to four rounds of percent-decoding; it cannot prove
that code which builds or decodes an address at run time holds none.
This inspection does not need an org connection. Report detected category, file and
line; never echo the matched secret. Inspect the installed help for exit semantics
and distinguish a missing/unreadable file from a clean scan.

A clean pattern scan is not a full secret audit or proof that a replay is safe to
publish. Inspect fixture inputs, screenshots, embedded URLs and any generated outputs
included in the requested review. If removal is requested, replace embedded credentials
with runtime authentication and verify the sanitized script remains meaningful.
Do not claim a credential was revoked merely because its literal text was removed.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
