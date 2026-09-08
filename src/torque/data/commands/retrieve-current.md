---
description: "Retrieve current metadata, preserve the baseline and stage an editable proposal."
---

# /retrieve-current

Retrieve current metadata, preserve the baseline and stage an editable proposal.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Resolve target and exact metadata names from the request and live schema. Identify
the Salesforce DX project and a task-specific private artifact directory. Preserve
unrelated dirty files; do not retrieve over the working proposal.

Use the available Salesforce retrieval tool or installed CLI. In a valid DX project:

```sh
sf project retrieve start --metadata <Type:ApiName> --target-org <org-alias> --output-dir <current-directory> --json
```

Repeat supported selectors or use an explicit manifest for several components. Check
the command result and saved files for missing/partial retrieval. Record org identity,
time, selectors, API version and file references. A cached local copy remains usable
for offline planning when clearly dated; it is not automatically current live state.

Copy the required baseline files to a proposed directory, make the requested changes
there, and retain the original. Compare the exact diff and reconcile intervening org
changes before applying it. For a new component record its verified absence and
retrieve only dependencies needed to implement it. Continue with `/validate-change`
and normal deployment when implementation is part of the request.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
