---
description: "Validate the exact proposed change with relevant static, deployment and functional checks."
---

# /validate-change

Validate the exact proposed change with relevant static, deployment and functional checks.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Identify the target, unchanged manifest or explicit component list, working DX project,
and expected outcomes. Preserve a scoped current baseline. Select validation based on
the change; do not run an exhaustive release ceremony for a small reversible edit.

Start with the project's documented validation/CI path and current official
Salesforce guidance or skills for the metadata involved. A configured CumulusCI,
sfdx-hardis or commercial pipeline can supply the same technical observations;
retain its exact job, target, source and artifact scope rather than rebuilding its
steps in Torque. If using another tool's report, label that provenance instead of
pretending it passed a Torque verifier. Check installed tool help when commands
or platform integrations have changed.

For Apex/LWC, inspect installed analyzer availability separately from its execution.
Installed plugin lists may include uninstalled/JIT placeholders. Use the actual
installed version's help and command schema; record findings and missing/failed
reports instead of treating an exit code alone as proof of cleanliness.

For metadata, use the current Salesforce CLI or an available deployment tool for
validation, for example in the selected DX project:

```sh
sf project deploy start --manifest <package.xml> --target-org <org-alias> --dry-run --json
```

Record the exact validation job, selectors, component failures and test results.
Test relevant Apex and user paths. `/qa` can route additional checks; skipped or
unsupported surfaces remain visible and require no QA token. A failed validation
needs diagnosis, not an invented success or automatic bypass of the failed checks.

Check dependencies and summarize what passed, failed, was not applicable, or remains
untested. When deployment is already authorized, continue once its material errors
are resolved; do not add a second Torque approval step. Otherwise leave a concrete
prepared result. Follow an actual deployment with `/verify-change`.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
