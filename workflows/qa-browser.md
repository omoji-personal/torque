# /qa-browser

Run a selected browser journey and verify its actual user and side effects.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

Resolve the requested journey, org and intended user. Inspect the installed browser
library/CLI help for available journeys; do not invoke a made-up journey as a discovery
mechanism. Generic packaged checks and client-specific private tests have different scope.

```sh
torque browser browser <flow-name> --target-org <org-alias> --workspace <private-path> --client <client-name>
```

Add `--headed` when helpful, except under a browser window a delegated approver granted: there Torque's browser runs headless only and `--headed` is refused. This native route's single-profile mode uses its configured
admin authentication; label it admin-only unless a separate intended-user session was
actually established. For a different user or arbitrary browser task, use the available
host/browser automation directly or `/qa-multiprofile`; do not claim unsupported native
journeys are implemented.

Respect one driver per active browser session. Reuse or isolate the operator's session
without closing unrelated tabs or changing accounts silently. Verify identity after
login/switches. Browser journeys can write records: establish side effects and cleanup
within task scope. Do not paste session cookies or frontdoor URLs into logs.

Save manifest/screenshots privately, distinguish real UI events from backend diagnostic
calls, read back meaningful side effects, and report missing coverage and cleanup survivors.
