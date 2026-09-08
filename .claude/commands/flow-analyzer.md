---
description: "Explain, review and improve a Flow from its current definition and actual behavior."
---

# /flow-analyzer

Explain, review and improve a Flow from its current definition and actual behavior.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Locate the requested Flow in the selected org or supplied metadata. Retrieve its current
definition and identify the active version separately from the latest/draft version.
Use `/advisory flow` for definition-scoped activation evidence when available.

Explain purpose, entry conditions, trigger timing, screen path, decisions, variables,
subflows, formulas, DML and external actions. Tie important statements to elements.
Review bulk behavior, queries/DML in loops, recursion, fault handling, permissions,
transaction boundaries, hardcoded identifiers, nulls, naming and maintainability.
Consider a simpler field/formula/before-save approach only when functionally equivalent.
Do not require an employer-specific bypass object or a bypass in every Flow.

Distinguish source findings from observed runtime behavior. If the user asked for a
fix, preserve current metadata, make a targeted edit, validate it, test relevant paths,
deploy/activate within the existing authorized scope and verify the intended active
version and behavior. Do not replace the whole Flow just because XML generation is easy.

Save the explanation, change rationale, tests and unresolved paths. For discovery or
training use plain-language steps; for implementation include exact elements and diffs.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
