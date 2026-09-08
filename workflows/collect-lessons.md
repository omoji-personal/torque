# /collect-lessons

Curate selected lessons into reusable knowledge without merging private client facts.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Read lessons from the chosen workspace/client and any files explicitly supplied
for this curation. Do not scan every user's directories by default. Compare with
current relevant rules, workflow docs and knowledge to avoid duplicate or obsolete advice.

For each candidate identify category, source/date, evidence strength, current
applicability and scope: client-specific, firm practice, or general platform knowledge.
Prefer a short reproducible explanation over a universal “always/never” rule.
Preserve conflicting evidence and version boundaries rather than quietly overwriting them.

If the user requested curation, write the suitable destination within that scope:
client findings remain private; general lessons can be re-authored as portable
knowledge with identifiers, credentials and proprietary record content removed.
Adding a public source file is distinct from publishing it. Do not commit, push,
send, or clear source lesson files as an implied final step.

Report what was integrated, already documented, scoped to a client, or still uncertain,
with source provenance. Archive processed records only when requested; no required
queue, automatic cross-client learning or model-specific reviewer is introduced.
