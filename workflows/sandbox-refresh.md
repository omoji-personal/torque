# /sandbox-refresh

Reconnect and restore the intended sandbox configuration after an actual refresh.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Identify the sandbox that was refreshed; this recipe does not initiate a refresh.
Verify its current org identity, authentication, username/domain and client mapping.
Reconnect using `/connect-org` if required. Do not assume an old JWT certificate,
connected application, org ID or alias remains valid after refresh.

Read the approved pre-refresh sandbox baseline and compare relevant environment
settings, package versions, active Flows, test users, integrations, scheduled work,
email/delivery behavior and data scope. Source-production values may be inappropriate
for a sandbox. Discover the client's actual settings; no product-specific defaults.

Carry out the restoration work already requested, keeping exact pre-change values
and recording changes. If the baseline is missing, present the specific uncertainty;
do not recreate former values from memory or copy live production secrets.
Use representative record counts and samples appropriate to full/partial/developer
copy type without treating every count difference as a defect.

Run scoped `/audit-org` and `/verify-change` checks, including the intended users and
integration destinations. Save updated connection/context and a refresh report with
restored, unchanged, intentionally different and unresolved items.
