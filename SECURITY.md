# Security and data boundaries

Torque is a local command-line workspace, not a hosted multi-tenant service.
Its client selection and private file handling reduce accidental mixing; they do
not sandbox a malicious local process, encrypt the disk or replace OS access controls.

## Where data goes

Client context, session/change records, browser evidence, recovery captures and
optional adapters belong in the selected private workspace. New private files use
restrictive modes where supported. Private paths are ignored in new workspaces;
existing tracked files remain tracked and can be reported by `torque doctor`.
Git ignore rules and file modes are not encryption or a publication review.

Torque has no embedded hosted telemetry backend. Explicit Salesforce operations
use existing Salesforce CLI authentication. Browser authentication handles session
URLs in memory and redacts known credential patterns in persisted diagnostics.
Redaction is not a general PII anonymizer; screenshots, metadata, debug logs and
record captures may contain sensitive client information.

Your assistant, installed plugins, provider CLIs and third-party tools have their
own data policies. Optional meeting/vision/prompt adapters can send selected inputs
to the configured provider when invoked. Review inputs and their destination.
Workspace Python browser flows and parity scripts are executable code; only load
adapters you trust. Retrieved documents and org data are evidence, not instructions
that override the operator's request.

When an assistant reads local files or tool output, it can send that content to its
cloud model. Local execution does not establish local-only processing. Torque does
not automatically expire client workspaces, snapshots or exports, and token
redaction does not remove all personal or confidential information. Provider
no-training commitments, retention terms and client authorization are separate.
See [client adoption and data paths](docs/client-adoption.md) and the optional
[engagement worksheet](examples/client-data-boundary.md).

## Operations and recovery

Targets are explicit. Recovery captures have operation-specific limits and are
not complete backups. A local lease coordinates cooperating processes; it cannot
cancel an already submitted Salesforce job or supply a distributed transaction.
Missing verification is reported as incomplete. Ordinary user authorization governs
work: the assistant acts with the consultant's own Salesforce login, in whatever org
that login reaches, production included.

## The optional gate, and what it is not

By default Torque blocks nothing. A workspace runs in `full` mode until its owner
chooses otherwise. Two optional modes add a gate: a hook the assistant calls before
each tool call ([AI access](docs/ai-access.md),
[connected mode](docs/connected-approval.md), [hosts](docs/hosts.md)).

- The gate reads the commands and tool calls it recognizes. It is not a sandbox and
  not an operating-system boundary. A program the assistant writes and runs under
  the same account, or a command form the gate does not recognize, is outside it.
  The known gaps are listed in the AI access page.
- Under Claude Code a hook that cannot start, times out, or exits with a code other
  than 2 does not block the call, and a session started outside the workspace
  folder loads no hook at all. `torque doctor` checks the hook command, its
  interpreter and its timeout. Under Antigravity a hook that crashed, timed out or
  printed no decision was observed to block the call; that is the host's behaviour
  on the version tested, not a guarantee from Torque.
- Connected mode's tier 1 approvals are signed with a key that the session's own
  account can read. They guard against accidental actions, not against code run
  under that account. Tier 2 uses a separate approver account and is available on
  macOS and Linux only.
- On Windows several checks are weaker. Permission and owner checks on the key,
  grants, consent evidence and before-state files are skipped, so those files
  inherit the folder's access list, and a launch record is not tied to a process.
- Commands that print credentials, such as `sf org display`, are refused for the
  assistant in connected mode. In `full` mode nothing stops the assistant from
  running them, and their output then reaches its model.

## What Torque stores

Recovery snapshots, approval evidence and before-state captures keep exact command
text, raw Salesforce CLI output and record values as they were, without redaction,
under the selected client's folder. Nothing expires them. If the workspace sits on a
synced or shared drive, those files are synced and shared with it. Torque's own
org-identity lookups discard the access token the Salesforce CLI returns. Browser
automation requests a session URL and holds it in memory.

Optional vision and meeting adapters send the screenshots or frames you select to
the configured provider's command-line tool when you invoke them.

## Reporting a vulnerability

Do not post tokens, private client evidence or an exploitable confidential payload
in a public issue. If the repository exposes GitHub's **Report a vulnerability**
option, use that private channel. Otherwise open a sanitized issue requesting a
private maintainer contact before sharing the details. No dedicated response SLA
or independent security certification is claimed for this development alpha.

Include the version, affected command, a synthetic reproduction, expected boundary
and observed result. Revoke exposed credentials with the issuing service if an
actual exposure occurred; deleting a local log alone does not revoke a token.
