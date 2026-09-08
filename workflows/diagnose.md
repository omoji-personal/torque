# /diagnose

Diagnose org, tool, deployment, context or access issues and carry through authorized fixes.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Start from the observed symptom and expected behavior. Reuse error text, task history,
org and user supplied in context. Classify the likely path: connection/tool setup,
deployment, missing client knowledge, permissions, automation, or data.

1. Resolve the intended org and actual identity; check only relevant tool/dependency
   availability, auth and configuration. Redact credentials in diagnostic output.
2. For a deployment, inspect the exact job and component failures, whether it was a
   validation or real deployment, dependencies, current source, and partial successes.
3. For access, identify the intended user and operation, then distinguish CRUD/FLS,
   permission assignments, groups/muting, record access, and UI visibility. An admin
   describe or one missing permission set grant does not establish effective access.
4. For automation/data, read current metadata, entry criteria, relevant logs and job
   results. Use `/flow-analyzer`, `/logs`, `/security-review` or `/compare-orgs` as useful.
5. For context problems, verify workspace/client selection, referenced files and freshness.

Report observed cause versus remaining hypotheses. If fixing is part of the request,
make the scoped change via normal tools, capture needed pre-state, run relevant
validation and verify the reported symptom. Do not automatically disable validation
rules, grant admin rights, delete a checkout, or install global dependencies as a generic fix.
Save the outcome and any reusable finding to the client session.
