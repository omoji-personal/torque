---
description: "Use platform notes, Flow activation, impact and evidence helpers without changing the org."
---

# /advisory

Use platform notes, Flow activation, impact and evidence helpers without changing the org.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

Interpret the request as a supported advisory subcommand. Inspect `torque advisory
--help` and the selected subcommand's help for its exact flags. Common calls:

```sh
torque advisory notes --command <command-text> --json
torque advisory flow --target-org <org-alias> --api-name <FlowApiName> --json
torque advisory impact --target-org <org-alias> --sobject <Object> --json
torque advisory evidence --target-org <org-alias> --field <Object.Field> --json
```

`needs` and `receipt` are also available through the package interface. Pass arguments
as separate values, not a shell expression. For client work select the private workspace
as described by the installed CLI; standalone reads can use an explicit org.

Summarize the decision-relevant findings, sources, dates and unknowns. A receipt is
evidence about its covered checks, not a write authorization or a complete outcome
proof. Do not infer intended-user access from assignments to somebody else, or
machine observation from a browser/UAT assertion. Strict exit behavior is optional
for callers that request it; the ordinary conversation remains advisory.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
