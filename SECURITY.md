# Security

Torque makes a security claim, so it owes you a way to challenge it.

## Found a bypass?

**That is the point of the project.** Every attack fixture in `harness/tests/` exists because a
review found a way through an earlier version of this code. Yours would be the next one.

- **Something that defeats a stated invariant** — email **omid.mojtahedi@gmail.com** with the
  command or tool call and what you expected to happen. Please don't open a public issue first;
  I'll confirm within 72 hours, and I'd like a fix and a regression fixture in place before it's
  public. 90 days is a fair ceiling — after that, publish regardless.
- **Anything else** (a false denial that blocks legitimate work, a broken first run, a doc that
  overclaims) — open a public issue. Those aren't sensitive and I'd rather they were visible.

If you send a bypass, tell me how you'd like to be credited. Fixtures carry attribution.

## What is in scope

The invariants the gates claim to hold, all enforced on the agent's tool surface
(Bash / Edit / Write / Read / MCP):

1. No Salesforce write reaches a non-allowlisted or production org without an operator override.
2. The agent cannot mint an approval token or session grant, and cannot read the signing secret
   or the `sf` CLI auth store.
3. Destructive operations require an operator-present token, on both the Bash and MCP surfaces.
4. The gate files and the trust anchor cannot be modified by the agent.
5. A gate that crashes or times out denies rather than allows.
6. The production override cannot be forged, replayed, or widened.

## What is explicitly NOT in scope

These are documented limits, not undiscovered holes — see the threat model in
[`guide/Torque-Guide.pdf`](guide/Torque-Guide.pdf) §05:

- **Arbitrary code executed as the same OS user.** Reading `~/.torque/secret` via `/proc`,
  `ptrace`, or a compiled binary; forging a login session with a purpose-built program. A
  PreToolUse hook cannot adjudicate that — it is a credentials and OS-trust boundary.
- **`sf` spawned as a subprocess of a script the agent writes and runs.** The hook sees the
  script's invocation, not what the script spawns. Closing this needs a PATH-level shim that
  classifies before `exec`; it is the v2 roadmap and is **not built yet**.
- **Anything upstream of the credentials.** If a production org is authenticated with write
  permissions in an autonomous session, Torque narrows the blast radius; it does not remove it.
  Connect production read-only.

Reports that land in the "not in scope" list are still welcome — if you can show one is easier
to reach than the threat model implies, that is a real finding about the documentation.

## Verifying the claims yourself

```
python3 bin/torque-demo                     # ~3s, no org, no credentials
python3 harness/validate.py --self-test     # neuters each guard; the attack must then succeed
```

The self-test is the honest one: a check that cannot fail proves nothing, so each guard is
temporarily broken on purpose and the corresponding attack is *required* to get through.
