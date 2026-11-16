---
description: "Use platform notes, Flow activation, impact and evidence helpers without changing the org."
---

# /advisory

Use platform notes, Flow activation, impact and evidence helpers without changing the org.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

Interpret the request as a supported advisory subcommand. `torque advisory --help` lists every
subcommand, what a receipt covers and what `--strict` changes; the selected subcommand's help
gives its exact flags. Common calls:

```sh
torque advisory notes --command <command-text> --json
torque advisory flow --target-org <org-alias> --api-name <FlowApiName> --json
torque advisory impact --target-org <org-alias> --sobject <Object> --json
torque advisory evidence --target-org <org-alias> --field <Object.Field> --json
```

Pass arguments as separate values, not a shell expression. For client work select the private
workspace as described by the installed CLI; standalone reads can use an explicit org.

Summarize the decision-relevant findings, sources, dates and unknowns. Do not treat a receipt as
a write authorization, infer intended-user access from assignments to somebody else, or infer
machine observation from a browser/UAT assertion. The ordinary conversation remains advisory.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
