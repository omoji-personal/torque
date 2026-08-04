# Experiments — measurements that have not become checks yet

A check runs in a profile and its verdict is load-bearing. An experiment is how a claim gets
established in the first place, before anyone knows what the right assertion is.

The distinction exists because of a specific failure. `torque done` shipped with two of its six
layers unable to return a row — `soql()` passed `--use-tooling-api` on every query and the
Tooling API refuses `FieldPermissions` — and every offline check passed, because against an
unreachable org a broken query and an empty result look identical. It took one run against a real
org. Wiring an unverified org-touching check straight into the capability profile means the next
run may go red for reasons that are the author's bug rather than a finding, and a profile that
cries wolf is a profile people stop reading.

So: experiment first, run by an operator, output read by a person. If the measurement holds, it
becomes a catalogue entry with a runnable verifier, and only then a check.

Nothing here runs in any profile. Nothing here is evidence until it has been run and its output
recorded.
