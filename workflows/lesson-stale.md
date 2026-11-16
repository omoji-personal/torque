# /lesson-stale

Take a wrong or outdated lesson out of recall at once, with the reason, keeping its history. Use it when a lesson Torque showed you no longer holds.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

`torque lesson stale <id> --reason <why> --workspace <private-path> --client <client-name>`.
`torque lesson stale --help` says how to name the lesson that replaces it and how to undo the mark.
Every lesson command is in `/lesson`.
