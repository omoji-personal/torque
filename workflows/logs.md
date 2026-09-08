# /logs

Analyze supplied Salesforce debug logs and relate findings to the reported problem.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

Inspect `torque logs --help` for the installed analyzer's exact input/output options.
Use a supplied local log or retrieve the specifically requested org/job/user logs
through ordinary Salesforce tools. Acquiring logs and analyzing a local file are
distinct actions; avoid broad trace-flag changes just to inspect an existing log.

```sh
torque logs --log-file <private-log-path> --json
```

The native `--target-org` mode fetches a recent log, or a bounded recent set with
`--since <ISO-timestamp>` and `--limit <count>`; it is not exact job/user correlation.
`--since-deploy` is a recent-time-window approximation. Retrieve the exact desired
log separately and use `--log-file` when that stronger correlation is required.

Identify relevant errors, transaction boundaries, execution paths, repeated queries/
DML, limits and automation interactions. Preserve the timestamp/user/request context
and separate heuristics from demonstrated causes. A log without an error does not
prove the UI or business outcome worked, and a heuristic warning may be benign.
The analyzer's numerical score summarizes its heuristics, not overall org health.

Keep raw logs in the client's private artifacts; summaries should contain only the
necessary sanitized evidence. Correlate with current source/metadata and the actual
failed task, carry through authorized fixes, and re-run relevant verification.
