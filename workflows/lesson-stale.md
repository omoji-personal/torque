# /lesson-stale

Mark a specific lesson outdated while preserving its history.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

Use the selected client and supplied lesson ID:

```sh
torque lesson stale <lesson-id> --workspace <private-path> --client <client-name>
```

Record why the lesson is no longer applicable when context provides that evidence.
The inherited store increments stale state and may archive after repeated marks;
inspect the actual result rather than promising immediate deletion. Keep the prior
record as history and create a corrected scoped lesson when useful. This action
does not edit public rules, other clients' lessons or model memory automatically.
