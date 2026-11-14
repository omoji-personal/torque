---
id: L-20261114-5e13
title: Save --json output on its own; do not merge the error stream into the file
scope: platform
kind: pitfall
state: active
when: you are about to save `sf ... --json` output to a file and read it back, or reading saved CLI JSON fails with "Expecting value" or a byte-order mark
do: redirect only standard output to the file (no `2>&1`), read the file as UTF-8 with a signature (utf-8-sig), and read a saved deploy result with `torque check result FILE`
because: the CLI writes warnings such as its update notice to the error stream, and Windows PowerShell adds a byte-order mark, so a merged or re-encoded file is not valid JSON
applies:
  tasks: []
  types: []
  commands: ["sf * --json*2>&1*"]
  keywords: ["--json", "2>&1", utf-8-sig, update warning]
  errors: ["Expecting value: line 1 column 1", "Unexpected UTF-8 BOM"]
evidence:
  - {type: session, ref: "field-reported, one consultant's CLI transcripts over 2.5 weeks: 12 failed parses of saved CLI output and 4 failed reads of deploy results in 5 sessions", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: the saved file starts with `{`.
