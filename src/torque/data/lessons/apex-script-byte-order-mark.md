---
id: L-20261114-5e0e
title: A file written by Windows PowerShell can start with a byte-order mark that Apex rejects
scope: platform
kind: pitfall
state: active
when: anonymous Apex fails with "Invalid identifier" at line 1, column 1, or you are about to write a script or metadata file from Windows PowerShell 5.1
do: write script and metadata files with an editor or file tool that writes UTF-8 without a byte-order mark, and strip the mark from a file that has one
because: "`>`, Out-File and Set-Content -Encoding utf8 in Windows PowerShell 5.1 add a byte-order mark, which the Apex compiler reads as an invalid character"
applies:
  tasks: [apex]
  types: []
  commands: []
  keywords: [byte-order mark, BOM, PowerShell, Out-File]
  errors: ["Invalid identifier"]
evidence:
  - {type: session, ref: "field-reported, one consultant's CLI transcripts over 2.5 weeks, orgs not counted: 5 failed Apex runs in 2 sessions; 7 and 28 tool calls to recover", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: the file's first three bytes are not EF BB BF.
