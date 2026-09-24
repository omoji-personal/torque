# Try Torque without a Salesforce account

The offline demo gives you a complete sample consulting thread: discovery notes,
a requirement with three acceptance criteria, a proposed Salesforce change,
synthetic examples, a QA plan, an evidence ledger and a resumable handoff.
It needs only the installed Torque package. It does not launch Salesforce CLI,
a browser, a model provider or a paid service.

Create it in a **new directory outside the Torque source checkout**. Its parent
must already exist. The demo refuses existing directories, including empty ones,
and will not merge with existing client files.

```sh
torque demo /absolute/private/torque-demo
```

For machine-readable paths, add `--json`. Open the returned `START-HERE.md`.
The created workspace is generic and its only client is explicitly named
`synthetic-community-center`; its org is unconfigured.

From the created directory, explore the example:

```sh
torque context --workspace . --client synthetic-community-center
torque workflows show discovery
torque workflows show prep-changeset
torque workflows show qa
torque session list --workspace . --client synthetic-community-center
torque handoff --workspace . --client synthetic-community-center
```

These commands read local context and working recipes. The scenario concerns a
fictional community center: staff need to record whether someone prefers Phone
or Email contact. The example leaves real design decisions open, including
requiredness, page placement and the intended staff role.

The client's `project/` is a small SFDX starter with a new demonstration object,
a picklist field, a narrow permission set and an exact package manifest. It is
prepared source, not a deployed application. The sample API version and schema
must be reviewed for a future real org; the demo creates no record page or user
assignments.

`artifacts/evidence.json` records two actual local observations: the generated
XML parses, and the synthetic values occur in the prepared picklist. Every live
acceptance criterion and deployment is labelled `NOT_RUN`. There are no fake
Salesforce records, job IDs, screenshots or verified-session entries. The native
journal contains only `prepared` and `incomplete` entries, and the handoff is
rendered from that journal.

The client also gets four scripted examples under
`clients/synthetic-community-center/examples/`. Each folder holds an invented
`input.md` (marked `SYNTHETIC EXAMPLE`) and a `walkthrough.md` that applies one
workflow recipe to it. The walkthroughs are prepared text, not live agent output,
and every conclusion in them is labelled as proposed until checked in a real org.

| Folder | Workflow | Input |
| --- | --- | --- |
| `alert-triage` | `/triage-alert` | A flow fault notification. The walkthrough reaches a likely cause to confirm, a proposed fix, a retest plan and a draft client note that is sent only once the cause is confirmed. |
| `gift-payments` | `/gift-payments` | A request to stamp a processed date on payments from a closed gift batch. The walkthrough gives a flow design, bulk-safety notes, a three-case test plan and a rollback. |
| `grants-outbound-funds` | `/grants-outbound-funds` | A request to create award records when a funding request is approved. The walkthrough covers the objects, fields not writable on insert, a flow outline, test data and acceptance criteria. |
| `requirements-to-build` | `/requirements-to-build` | A stakeholder note about volunteer shift sign-ups. The walkthrough turns it into user stories, acceptance criteria, a proposed metadata list, a test script and open questions. |

Continue by editing the sample requirement or recording a local decision:

```sh
torque session add --workspace . --client synthetic-community-center \
  --summary "Sample design decision: keep contact preference optional; intended staff role still unresolved." \
  --status prepared
torque handoff --workspace . --client synthetic-community-center
```

For actual client work, create a separate client and explicitly select its org.
Keep this demo's synthetic evidence separate from live observations. All generated
files are private by default and the workspace ignores its contents in Git.
If generation fails, Torque removes the new directory it created.

Developer interface: `torque.demo.create_demo(destination: Path) -> dict` returns
workspace, client, client_root, start_here, project, evidence and handoff paths,
plus `synthetic: true`, `status: prepared` and `org_calls: false`. It uses the
existing workspace API and standard library; no additional package data or
runtime dependencies are required.

The demo also creates an optional engagement change. `--json` returns its
`change_id`; use `torque change show ID --workspace . --client synthetic-community-center`
to inspect three unrun criteria, the proposed decision and the next action.
The generated handoff includes this record. No acceptance result is marked passed.
