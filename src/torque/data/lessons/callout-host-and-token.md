---
id: L-20261114-5e12
title: Register the host before a callout, and keep tokens out of the address
scope: platform
kind: pitfall
state: active
when: you are about to call an outside service from Apex or anonymous Apex, or a callout fails with "Unauthorized endpoint"
do: reach the host through a named credential (or register it as a remote site), and send any token in a header or the request body, never in the URL
because: an unauthorized-endpoint error prints the whole address, so a token in the URL lands in the output and the logs
applies:
  tasks: [integration, apex]
  types: [NamedCredential, RemoteSiteSetting]
  commands: []
  keywords: [callout, access_token, remote site setting, named credential]
  errors: ["Unauthorized endpoint"]
evidence:
  - {type: session, ref: "field-reported, one consultant's CLI transcripts over 2.5 weeks: 1 unauthorized-endpoint failure and 3 credential-access errors caught by scripts", date: 2026-11-14}
  - {type: session, ref: "field-reported, one consultant's working notes over eight weeks: 1 note: a failed callout printed its endpoint with the token in it", date: 2026-11-14}
source: harvested
created: 2026-11-14
last_verified:
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Check: no endpoint string in the code or the script holds `token=` or `access_token=`.
