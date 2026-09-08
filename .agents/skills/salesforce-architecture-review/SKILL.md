---
name: salesforce-architecture-review
description: Review a Salesforce data model, automation design, integration, access model, or cross-component solution against business outcomes and maintainability.
---

# Salesforce architecture review

Start with the business outcome, current design, constraints, and evidence.
Inspect available source and client documentation. If current org evidence is
needed, use an explicit target through the existing Salesforce tools.

Separate managed-package behavior, subscriber customization, client decisions,
and unverified assumptions. Review only the relevant concerns: automation
ownership, data lifecycle, permissions, integration failure handling, volume,
support burden, and how someone will verify the outcome.

Recommend the smallest maintainable design that meets the requirement. Explain
tradeoffs only when they change the decision. Cite the evidence behind material
findings and identify what still needs confirmation. Do not turn design review
into evidence that the implementation works. When implementation is also
requested, continue with the agreed design and ordinary execution tools.
