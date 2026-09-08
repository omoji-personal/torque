# A synthetic consulting walkthrough

This example describes a workflow to exercise in a sandbox. It is not a claim
that these org operations have been performed.

Create a private workspace and add a client referring to an org alias you have
actually authenticated. Open that private workspace in your assistant.

**Resume and scope:** “Resume Sample Client. We need a new required field in the
onboarding Flow, with clear help text and access for the support team.”

The assistant loads the client's current context and sessions, identifies the
Flow and intended users from source/current metadata, and resolves any missing
business requirement. Torque supplies the context and relevant review recipes;
the existing Salesforce tools supply org evidence.

**Implement:** “Make the change in the sandbox and validate the complete flow.”

The assistant retrieves current metadata, prepares the change, runs relevant
analysis and validation, performs the authorized deployment, and verifies the
specific resulting behavior. An optional snapshot wrapper can preserve a
recoverable pre-state. Neither an advisory receipt nor a QA routing suggestion
substitutes for the user's authorization or the actual work.

**Verify:** “Check it as the support user and verify the saved record.”

The assistant checks actual session identity, renders the workflow, and checks
the relevant saved values. A system-admin query alone cannot establish what the
support user saw. If browser setup or a test user is unavailable, the assistant
reports the missing evidence and continues the independent checks it can run.

**Continue later:** “Save our work and prepare a handoff.”

The assistant records the changed paths, deploy/test identifiers, outcomes,
remaining work, and useful recovery references in the client's private context.
The journal marks status as supplied by its writer; a generated handoff retains
that distinction. The next conversation can resume without rediscovering the
engagement or loading another client's material.

For a completely local executable demonstration of setup, context isolation,
and handoffs, run `python scripts/smoke-installed.py` from an installed checkout.
It uses temporary synthetic clients and never connects to Salesforce.
