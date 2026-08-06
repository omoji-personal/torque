# Observe-only — measure the gates before you trust them

```
torque approve --observe 90      # operator, from a REAL terminal. ≤120 min.
torque approve --end-observe     # revoke early
```

**`torque` has to be on PATH first**, or that reads `command not found` — which is how it first
reached a real operator. `bin/torque` exists precisely because every deny message says "run
`torque approve …`", and nothing was putting it where a shell would find it:

```
export PATH="$HOME/.torque/shim:<repo>/bin:$PATH"     # in your shell profile
```

`torque install-gates --shim` prints the line with both paths filled in. Everything below can
also be run as `python3 bin/torque approve …` from the repo root.

For the duration, a gate that would have denied an operation **records it and lets it through**.
Denials become `OBSERVE` entries in the audit trail carrying the reason they would have given.

## Why this exists

Torque is new. It has 216 gate fixtures, 17 mutators and three audit lenses, and all of that is
evidence it does what it claims — not evidence that anyone can get a day's work done through it.
Those are different claims, and only the first had been established.

The first attempt at the second was a backtest: replay six months of real client commands through
the classifier offline. That found one defect responsible for 854 of 955 denials, and a design
problem responsible for most of the rest. What it could not find is the friction that only shows
up live, because a replayed command is not a session — it never adapts, never rephrases, never
asks for approval.

Only a live session does that. And nobody sensibly runs a live session through a gate they have
not first watched. So: compute the decision, record it, let the operation through. A WAF in
detection mode, an IDS before it goes inline. The ordinary way a blocking system earns the right
to block.

Afterwards, `local/audit.log` holds every decision the gates would have made against real work.
That is the number worth having before deciding what to change.

## What it does not do, and why the constraints are the argument

Observe-only is a bypass. Two properties keep it from being a hole:

**The grant lives in the trust anchor.** Signed with the anchor secret, time-boxed, bound to this
tree, mintable only by an operator at a real login TTY whose ancestry is clear of the agent. The
agent's Bash, Edit, Write and Read cannot reach `~/.torque`. It is deliberately not an
environment variable or a config flag — the agent composes its own Bash and therefore its own
environment, so any switch it can set is a switch it can set.

**It never applies to the trust boundary.** These stay refusals with any window open:

| still denied | why |
|---|---|
| reading `~/.torque` | a window that could reach the secret could mint itself a permanent one |
| reading `~/.sfdx`, `~/.sf` | live access and refresh tokens |
| editing Torque's own protected source | could delete the check enforcing the rule above |
| putting `local/` into git | publishes org findings and session logs |
| a gate crash or unparseable event | fail-closed must not become fail-open under observation |

**It is never silent.** Every observation is written durably *before* the operation proceeds. If
that write fails the gate denies instead — an observation nobody can read afterwards is
indistinguishable from the gate having been switched off.

## The prompt says the dangerous part

Authorizing it requires typing `PRODUCTION IS UNPROTECTED`, not something reassuring. For the
duration, a production write that would have been denied **will proceed**, and a destructive
operation with no approval token **will proceed**. Do not open a window against an org you cannot
afford to lose.

120 minutes is the ceiling, on the production-session clock rather than the 12-hour maintainer
one. Unlike the other two grants, this one is dangerous for its whole duration rather than at the
moment it is opened. It also has a natural length: what it is for is watching one real work
session and counting the interruptions. If two hours has not shown the friction, a longer window
will not — it will just be an unprotected afternoon.

## Verified by

`observe_grant_is_operator_only` — absent, forged, expired and wrong-tree windows must all still
deny; a valid one must record AND allow, asserting the reason rather than the exit code.

`observe_never_opens_the_trust_boundary` — the anchor stays denied under an open window, and the
trail carries the `OBSERVE` record. This one failed for real while it was being written: the
audit trail lives in `local/`, not the anchor, so the check was reading an empty directory and
would have passed a mode that recorded nothing.

Both are catastrophe-class and run in the static profile.
