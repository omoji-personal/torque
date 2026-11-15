# /collect-lessons

Review the lessons waiting for a decision and propose which to keep, retire or promote to the firm, a rule, a skill, a recipe or a check. Use it when the consultant asks, or when the context shows candidates waiting.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

`torque lesson review --workspace <private-path> --client <client-name>` lists what waits, repeats, needs a re-check or could be promoted, and changes nothing.
`torque lesson promote <id> --to firm` writes a proposal; apply it only on the consultant's word with `torque lesson promote --apply <proposal>`.
Every lesson command, and who applies which promotion, is in `/lesson`.
