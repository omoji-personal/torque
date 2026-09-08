# /prep-changeset

Prepare a dependency-aware deployment packet from the actual change.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Read the project's existing build and release configuration before preparing the
packet. Follow its source, test and promotion conventions, including an established
CumulusCI, sfdx-hardis or commercial pipeline when present. Preserve the selected
source revision, exact jobs and native outputs with the engagement change; Torque
does not require a duplicate release pipeline or a different package format.

Build the component inventory from the selected task's proposed source/diff, session
records, explicit user scope and exact existing deploy results. Do not assume every
recent commit belongs to the task. List type/API name, new/modified/deleted status,
dependencies, source path and destination.

Choose ordering from real dependencies: objects/fields, references, code/tests,
permissions, automation and UI. A modified page can be deployed by a reviewed merge;
it is not automatically a manual-only change. Activation and assignment steps depend
on the actual org/release process, not a universal fixed order.

Produce an exact manifest or supported selector set, manual steps, needed validation,
targeted tests, recovery references, deployment order and post-deploy checks. Preserve
the submitted manifest alongside its job ID; a job's success cannot authenticate a
later modified manifest. Mark destructive changes explicitly when in scope.

Save the packet in the private task directory and summarize it in the session. If the
request includes execution, carry the packet through the user's normal Salesforce
deployment tools and actual authorization; generating a checklist is not the end
of an implementation task. Use `/verify-change` for actual outcomes.
