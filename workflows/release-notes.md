# /release-notes

Write technical and client-facing release notes from verified change evidence.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Identify the product/project, requested version, source range, release state and
audience. Read supplied release notes, actual commits/diffs, deployment artifacts,
test results and relevant task records. Do not infer an installed release from
source commits or an upcoming release date from the file's timestamp.

Group new capabilities, improvements, bug fixes, behavior changes and administrator
actions. Internal notes retain exact components, dependency/migration order, testing,
known issues and rollback considerations. Client notes explain practical behavior,
who is affected and any action needed without unsupported outcome claims.

Check client-specific rollout/configuration before saying a change is available or
automatic. A package upgrade may still need configuration, assignments, migration,
activation or verification; list only actions supported by evidence.

Save privately with supplied organization/product branding and real support details.
When no branding is given use a neutral title and omit invented contacts. State
prepared/validated/deployed/available separately, and do not publish or send the
notes merely because their text is client-ready.
