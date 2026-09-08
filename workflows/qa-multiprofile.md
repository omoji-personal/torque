# /qa-multiprofile

Test the selected journey for configured users and compare role-specific behavior.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

Read the selected client's private test-user configuration. Use actual supplied test
users and authentication, not names or IDs from a bundled example. No passwords or
tokens belong in a seed file. Confirm the desired profiles/roles and expected access.

```sh
torque browser multiprofile <flow-name> --target-org <org-alias> --workspace <private-path> --client <client-name>
```

Inspect `--help` for current options and seed discovery. The native path uses admin
login followed by its supported user-switch mechanism. If that is unavailable,
use a separately authenticated permitted session and label the method accurately.
Observe each user's actual identity, not just a requested profile label.

Run shared-browser work serially. Independent authenticated sessions may run in
parallel only when their state and mutations do not interfere. Exercise positive,
negative and permission paths; compare screens, record results and error behavior.
Preserve the operator's original session and account for created test data.

Report per-user evidence and unsupported/unrun combinations. Admin-only coverage
does not satisfy end-user UAT; a backend query does not prove a rendered experience.
