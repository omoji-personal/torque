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
work; Torque does not install a global approval or command-blocking framework.

## Reporting a vulnerability

Do not post tokens, private client evidence or an exploitable confidential payload
in a public issue. If the repository exposes GitHub's **Report a vulnerability**
option, use that private channel. Otherwise open a sanitized issue requesting a
private maintainer contact before sharing the details. No dedicated response SLA
or independent security certification is claimed for this development alpha.

Include the version, affected command, a synthetic reproduction, expected boundary
and observed result. Revoke exposed credentials with the issuing service if an
actual exposure occurred; deleting a local log alone does not revoke a token.
