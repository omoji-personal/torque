# /lesson

Save a scoped lesson from supplied content or the current session.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

With content, capture the user's lesson directly. Without content, derive a concise
lesson from the actual session: symptom, cause, working resolution, scope and evidence.
The request to capture a lesson authorizes that useful local write; no automatic
capture hook, review ceremony or specific model is required.

```sh
torque lesson capture <lesson-text> --workspace <private-path> --client <client-name>
```

Keep client facts scoped to that client. General platform knowledge should identify
version/date, source and applicability; label an unverified hypothesis accordingly.
Remove credentials, record bodies and unnecessary identifying data from summaries.
Inspect the resulting record and report where it was saved. Package confidence or
promotion state is a storage convention, not proof that the lesson is universally true.
Use `/lesson-show` to inspect, `/lesson-helpful` to mark useful, and `/lesson-stale`
when later evidence invalidates it. No work waits for a lesson queue to be cleared.
