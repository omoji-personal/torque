# Using Torque with client information

Reviewed against the linked provider documentation on September 7, 2026. Provider
terms and feature coverage change; the client's agreement and the configuration
actually in use control an engagement. No employer or client account was inspected
to establish its retention settings for this guide.

Torque can support a consultancy's existing delivery process without requiring a
client to adopt a new hosted Torque service. Its local workspace preserves the
requirement, investigation, change evidence and handoff. Whether a cloud assistant
may see that information is a separate decision from Salesforce access.

## A business plan helps, but does not promise zero retention

OpenAI states that ChatGPT Business, Enterprise and API business data is not used
for model training by default. Its current Business policy also describes admin
retention controls and removal of deleted or unsaved conversations within 30 days,
subject to stated exceptions. A Business subscription is therefore not evidence
that nothing is stored. See [OpenAI enterprise privacy](https://openai.com/enterprise-privacy/).

In Codex, the authentication method matters: ChatGPT sign-in follows the applicable
workspace controls; API-key sign-in follows the API organization's retention and
sharing settings. Local clients support both; cloud Codex requires ChatGPT sign-in.
An API arrangement does not automatically cover activity performed under a ChatGPT
subscription. See [Codex authentication](https://learn.chatgpt.com/docs/auth).

For OpenAI API use, default abuse-monitoring retention can include customer content
for up to 30 days, with stated exceptions. Zero Data Retention (ZDR) requires
approval and configuration. Eligible endpoints and enabled features matter;
application state, connected services, image/file handling and notified model
exceptions must be considered. A request's `store=false` is not proof of approved
ZDR. See [API data controls](https://developers.openai.com/api/docs/guides/your-data).

Do not assume the newest model has the same storage behavior as an older model.
OpenAI documents model-dependent cache controls; cached key/value state differs
from raw prompts, but still needs to fit the client's requirement. A cache TTL is
not necessarily a maximum retention period. Verify the exact model and features
before making a promise. See [prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching).

Provider-side deletion also has multiple surfaces: ChatGPT Library files can remain
after their conversation is deleted, and project files have their own lifecycle.
Archiving is not deletion. See [chat and file retention](https://help.openai.com/en/articles/8983778-chat-and-file-retention-policies-in-chatgpt).

### Claude and Claude Code

Anthropic's commercial policy for Team, Enterprise and API customers excludes model training unless the customer explicitly opts in. Standard Claude Code inference retention is generally 30 days, subject to the stated exceptions; Team and standard Enterprise do not automatically provide ZDR. [Code data policy](https://code.claude.com/docs/en/data-usage)

ZDR requires organization-specific enablement and the correct authenticated route. Eligible API use or Enterprise Code inference can qualify; saved chat, stateful features and third-party integrations have separate rules. Covered Models normally require retention unless Anthropic explicitly authorizes an exception. Confirm the organization/workspace, model, endpoint and features; do not infer coverage from a subscription label. [API retention and eligibility](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention)

Local Code transcripts and tool output are plaintext. The normal 30-day cleanup does not cover everything: prompt history and auto memory persist separately, and Desktop/Cowork transcripts have distinct defaults. Provider ZDR does not delete these files or Torque's evidence. Check the installed client's documented settings and offboarding process. [Local application data](https://code.claude.com/docs/en/claude-directory#application-data)

Enterprise compliance copies are another store: when that capture is enabled, local session transcripts are retained by Anthropic for six years by default, or a finite organization conversation-retention period. Local sessions covered by ZDR are excluded. This is separate from inference retention and local disk cleanup. Account configuration and exported copies still need to be included in the engagement's data boundary. [Compliance transcripts](https://platform.claude.com/docs/en/manage-claude/compliance-sessions)

## Choose the smallest arrangement that meets the engagement

| Client requirement | Practical arrangement | Evidence needed |
| --- | --- | --- |
| Demonstration without client information | Torque's synthetic demo and original examples; no client org connection | Confirm that example files contain no copied client content. |
| Approved external AI, business no-training commitment sufficient | Firm-managed commercial assistant account, approved tools and normal client credentials | Applicable agreement, actual workspace/authentication method, data classes, permitted destinations and retention settings. |
| Client controls the approved AI environment | Work in the client's managed device or virtual environment with its approved account and tools | Client-granted access and destination policy; confirm which output may leave that environment. A client-owned machine alone does not stop model-provider processing. |
| Contract requires strict limits on provider retention | A provider-approved retention arrangement covering the exact account/project, model, endpoint and features | Written scope and exceptions, current configuration evidence, supported client authentication, and a synthetic verification run. Do not infer qualification from a plan label. |
| External AI access prohibited | Keep client operations in approved local/native tools; use external AI only with independently synthetic or adequately sanitized material permitted by the client | The allowed boundary, a human review of sanitization, and confirmation that the assistant cannot read excluded files or tool output. |

For a strict requirement that *no client information may leave a specified
environment*, an externally hosted model is unsuitable unless the client explicitly
allows that processing. An independently approved local model is another possible
deployment, but Torque has not qualified its model quality or a fully isolated
assistant setup. Do not imply that changing the model address supplies either.

Metadata can contain confidential business logic, names, email addresses,
endpoints or credentials. "Metadata only" reduces some exposure; it is not an
automatic confidentiality exemption. Aggregation, aliases and token redaction
also do not establish anonymization.

## Map the complete path

| Surface | What can be present | Operator responsibility |
| --- | --- | --- |
| Salesforce | Records, metadata, permissions, logs and native audit history | Use the engagement's authorized account and scope. Revoke access in Salesforce when it ends. |
| Local Torque workspace | Client notes, session/change journals, evidence copies, recovery snapshots, screenshots and exports | Approved device, storage location, access controls, backup policy and retention owner. Torque does not automatically expire this material. |
| Assistant context | Prompts, files read by the assistant, terminal/tool output, images and conversation history | Confirm the account and destination before using client information. Local execution can still send context to a cloud model. |
| Model provider | Inference inputs/outputs, relevant logs, state and caches | Match actual features and retention terms to client requirements. Confirm any exceptions or opt-in sharing. |
| Connectors, MCP servers and other tools | Retrieved or forwarded content, remote logs, synchronized indexes | Review each destination separately. A provider's agreement does not configure a third-party service. |
| Handoffs, tickets, repositories and backups | Copies of evidence and reports, including deleted files retained in history | Share only the intended material, account for copies and keep public examples synthetic. |

The [optional client data-boundary worksheet](../examples/client-data-boundary.md)
records this once per engagement and when the arrangement changes. It is a plain
document, not an automated enforcement or approval system. Reuse existing approved
policy when it covers the work; do not create a second approval for every command.

## What a consulting manager should require from a trial

Measure total delivery effort, including review, corrections, setup and support,
against the team's current process and existing AI tools. Record failed and
abandoned attempts. Have a second consultant resume and finish a task without
editing Torque's source. Use normal Salesforce permissions and deployment review.

Keep a pinned install, a named maintenance owner and a direct-tool fallback.
Torque's Markdown/JSON artifacts help portability, but this is not proof that a
colleague can operate it without help; demonstrate that in the trial. The
[validation record](validation.md) states the tested scope and gaps, and the
[benchmark protocol](benchmark-protocol.md) defines a fair comparison. No measured
productivity advantage or general production qualification is claimed.

At engagement closure, revoke source-system and provider access, stop any scheduled
work, return the agreed handoff, and carry out the documented retention decision
for workspaces, assistant history, files, connectors and backups. Keep required
audit evidence in approved storage. Disabling an account does not delete all copies.

## Capabilities and limits today

Torque has explicit client/org selection, private file handling, captured evidence,
operation-specific recovery and records that preserve incomplete outcomes. It has
no embedded hosted telemetry backend. It does not provide general PII detection,
automatic redaction of screenshots, provider-retention enforcement, disk encryption,
host isolation or a central tenant access-control service. See [Security](../SECURITY.md).

The useful distinction to demonstrate is continuity from requirement through
verified outcome and handoff using existing tools. A provider plan or an audit-style
interface is not a product differentiator by itself. Privacy claims should be
specific enough that a client can inspect the actual arrangement.
