# /lesson-show

Read saved lessons: the ones that fit the work, one in full, or a list by state. Use it before work earlier lessons may cover, or when the consultant asks what Torque has learned.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

`torque lesson for <words> --workspace <private-path> --client <client-name>` finds the lessons that fit the work.
`torque lesson show <id>` prints one in full; `torque lesson list --state candidate|active|retired|stale` lists them.
Every lesson command, and how to judge what you read, is in `/lesson`.
