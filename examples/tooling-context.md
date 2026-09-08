# Optional client tooling context

Copy only the useful parts into a selected client's `context/tooling.md`, or let
the assistant write a concise note from the actual project. This is an example,
not a required onboarding form. Do not insert invented tool versions or commands.

- Project/source: the actual repository path and its authoritative instructions.
- Delivery: the existing validation, build, test and promotion commands/configuration;
  identify the tool and version when relevant to reproducing an observation.
- Org scope: explicit aliases and their last verified identities; no credentials.
- Data migration: selected engine, reviewed mapping/config path and stable row keys.
- Evidence: where the chosen tools save job reports, test results and failure details.
- Open limits: what is unavailable, unverified or needs a different intended-user check.

Keep links to existing project instructions instead of copying a large configuration.
Record only what the next session needs. `torque context` reads client Markdown under
`context/`; other clients' notes remain outside the selected context. Tools mentioned
here are not installed or invoked by writing this file. A known command is not fresh
proof that its last operation passed.
