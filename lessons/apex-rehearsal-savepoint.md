---
id: L-20261114-5e10
title: In a rehearsal script, set the savepoint first and roll back to it at the end
scope: platform
kind: pitfall
state: active
when: you are about to run anonymous Apex that writes records only to see what happens and leave nothing behind
do: set a savepoint before the first write, do the work, read what you need, then roll back to that savepoint; return results in debug lines, not through a deliberate exception
because: "`Database.rollback(Database.setSavepoint())` at the end rolls back nothing, and a deliberate throw reads as a failure and cuts long results short"
applies:
  tasks: [apex, testing]
  types: []
  commands: []
  keywords: [setSavepoint, Database.rollback, rollback, rehearsal]
  errors: []
evidence:
  - {type: session, ref: "field-reported, one consultant's CLI transcripts over 2.5 weeks: 15 runs that threw on purpose to roll back and 1 script whose rollback did nothing", date: 2026-11-14}
  - {type: session, ref: "field-reported, one consultant's working notes over eight weeks: 4 notes on rolled-back probes", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: after the run, a count of the records the script created is zero.
