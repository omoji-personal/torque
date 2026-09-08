# /export-session

Export selected session work as an internal record, client update or change log.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Use the selected client and time/task scope. Read relevant session records, referenced
artifacts, actual diffs and current conversation. Do not gather all clients' activity
just because the date matches. Infer the requested audience/format; use Markdown
by default when the user has not specified a document format.

Internal record: objective, actions, component/data changes, verification, failures,
pending jobs, decisions and next steps. Client update: business effect, what changed,
what the client needs to do, and unresolved items in plain language. Change log:
exact changed components, deployment state, manual steps and verification evidence.

Use `/session-save` first if meaningful current work is not persisted. The native
`torque handoff` renderer may supply a starting record, but this export is a guided
audience-specific document, not an invented export subcommand.

Keep private recovery values and raw auth/query output out of shareable copies.
Do not invent duration, completion, release dates, adoption or sender identity.
Save to the private client artifacts directory and link the actual file. A prepared
client update is not sent until sending is specifically part of the request.
