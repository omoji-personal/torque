# Pre-schema-2 records — kept, not counted

Three files from before `torque.attestation/2`. They record a tree hash, a head, the toolchain
versions and a reproduce command. They do not record a **verdict**, a **profile**, or **which
checks ran** — which is to say they attest that a run happened, and not that it passed.

`bin/torque-attest`'s own docstring condemns exactly this shape: an artifact whose only job is to
be trustworthy, in a form a reader cannot check. Leaving them in `harness/attest/` alongside real
attestations meant a directory listing of 24 files where 21 carried a verdict, and nothing said
which was which.

They are moved rather than deleted because they are evidence of when the format changed, and
deleting the weaker records of your own history is the specific move that makes a project's
paper trail look better than the project was. `attestations_carry_a_verdict` now fails the build
if a file directly under `harness/attest/` lacks a schema, a verdict or a profile, so a fourth
one cannot appear quietly. This directory is excluded from that check by living one level down —
the exclusion is the point, and it is visible here rather than buried in a skip list.

Moved 2026-08-04.
