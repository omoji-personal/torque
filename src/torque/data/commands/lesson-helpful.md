---
description: "Mark a specific saved lesson useful and retain its original evidence scope."
---

# /lesson-helpful

Mark a specific saved lesson useful and retain its original evidence scope.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

Resolve the supplied lesson ID or unique supported prefix in the selected client:

```sh
torque lesson helpful <lesson-id> --workspace <private-path> --client <client-name>
```

Inspect the resulting lifecycle state. In the inherited store this may promote a
pending lesson or increment its useful count. Do not present promotion as independent
verification or widen a client observation into a universal platform rule. If an
ID matches several records, resolve the intended record before changing one.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
