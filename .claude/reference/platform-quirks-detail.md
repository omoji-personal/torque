# Platform quirks — detail (load on demand)

Each entry: symptom · cause · reproduction · date confirmed.

## sf apex run escapes the pipe (2026-07-31)
`sf apex run` renders `|` as `&#124;` in some output paths. Repro: run anonymous Apex that
`System.debug`s a `|`-delimited string; the shell sees the escaped entity. Use `~@~`/`~#~`.

## History NewValue/OldValue unfilterable (2026-07-31)
`SELECT ... FROM <Obj>History WHERE NewValue = 'x'` throws "field cannot be filtered".
Repro on any *_History object. Pull rows and filter in Python.

## purgeOnDelete required for repeated field probes (2026-07-31)
A custom field deleted via destructiveChanges without `purgeOnDelete=true` remains in the
object's deleted-fields queue for 15 days and its API name is reserved. Repro: deploy then
delete `X__c` twice with the same name — the second create collides. The probe cycle uses
run-scoped names AND purgeOnDelete; confirmed against sf-coffee.
