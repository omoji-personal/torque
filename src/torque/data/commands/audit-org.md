---
description: "Audit an org across configuration, automation, access, data and operational health."
---

# /audit-org

Audit an org across configuration, automation, access, data and operational health.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Determine the audit's business purpose and object/process scope. Use `/preflight-org`
to resolve identity, available metadata and package namespaces. The client profile may
name relevant objects, settings and checks; there is no built-in managed-package baseline.

Inspect applicable surfaces: installed packages and versions; custom settings/metadata;
integration endpoints without secrets; automation and active versions; trigger or validation
bypasses that actually exist; access configuration; relevant record counts and data quality;
failed async jobs; and documented operational dependencies. Bound query scope and report
omitted surfaces or access failures. Do not export entire sensitive record sets for a health check.

Use available MCP reads or `sf data query`, `sf sobject describe`, and metadata retrieval.
Discover real API names before querying. A missing package-specific setting is usually
not applicable, not an org defect. Compare findings to the client's intended process
and current source of truth, not another client's configuration.

Produce a prioritized report with evidence, business consequence, likely cause,
recommended correction and missing verification. Save it privately and append the
session delta. If remediation is requested, proceed through the relevant change workflow.
Do not claim a complete org audit from a handful of successful checks.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
