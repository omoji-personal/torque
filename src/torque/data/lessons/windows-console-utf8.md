---
id: L-20261114-5e14
title: On Windows, set Python's UTF-8 mode once, not per command
scope: platform
kind: works
state: active
when: a Python script or tool fails with "'charmap' codec can't encode" in a Windows console
do: set PYTHONUTF8=1 in the user's environment once (or run Python with -X utf8), and give every file read and write an explicit encoding
because: the Windows console uses a legacy code page such as cp1252 that cannot print many characters, and a per-command setting is forgotten in the next session
applies:
  tasks: []
  types: []
  commands: []
  keywords: [UnicodeEncodeError, cp1252, PYTHONUTF8, code page]
  errors: ["'charmap' codec can't encode"]
evidence:
  - {type: session, ref: "field-reported, one consultant's CLI transcripts over 2.5 weeks: 18 encoding errors in 9 sessions, although 295 commands set the variable for themselves", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: in a new console, `python -c "import sys; print(sys.stdout.encoding)"` prints utf-8.
