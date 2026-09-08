# /qa

Select and execute useful QA surfaces for the change, with explicit coverage and gaps.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

Start with the client's requested behavior and acceptance criteria. Identify the
intended user, positive and relevant negative cases, repeated edits/idempotence,
permissions and cleanup needs. Read the optional change record when present; do
not replace a meaningful business check with a metadata or process-exit check.

Reuse the project's existing tests and browser acceptance setup (including
CumulusCI/Robot or an established commercial suite) when appropriate. Keep their
actual report references and gaps in the change record; `change check` attachments
remain operator-reported unless a specific verifier evaluated them. Do not run two
browser drivers against the same session or rerun equivalent checks just to obtain
a Torque-branded result.

Use the change description and explicit target from the current task. The Torque
router is an additional aid for selecting coverage. Inspect its selected surfaces
and reasons; when useful, run the existing QA delegate:

```sh
torque qa run <change-description> --org <org-alias> --workspace <private-path> --client <client-name>
```

When verifying a deployment, pass its exact `--deploy-job-id` and unchanged
`--deploy-manifest`, or repeat `--deploy-component <Type:FullName>` for each exact
component. Check `torque qa run --help` for supported surface/change-type selectors.
No result may fall back to an arbitrary most-recent deploy.

Interpret each surface independently: passed, failed, manual, deferred, unsupported
or not applicable. Missing browser tooling or a skipped optional surface is not a
hidden success. Continue useful checks rather than requiring a token to proceed.
The optional legacy `/qa-token-*` helpers do not authorize changes and are never
required by the successor's normal workflow.

Tests may change records depending on the selected surface. Keep them within the
task's authorization and intended environment, label artifacts by actual user/profile
and fidelity, reconcile test records and verify cleanup. Save concise results and
continue fixing implementation defects when that is part of the request.
