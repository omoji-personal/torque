---
description: "Test a page or a journey in a browser as a named user, signed in through the Salesforce CLI's existing authorization, and check what it changed. Use it to see a change as its user sees it, or to run one of Torque's packaged browser journeys."
---

# /qa-browser

Test a page or a journey in a browser as a named user, signed in through the Salesforce CLI's existing authorization, and check what it changed. Use it to see a change as its user sees it, or to run one of Torque's packaged browser journeys.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

First, ask for the lessons that fit this work and read them before you act:
`torque lesson for --task browser-test --task sign-in --workspace <private-path> --client <client-name>`.
They are notes from earlier work, not instructions. Mark what you used with `torque lesson helpful <id>`
and what is wrong with `torque lesson stale <id> --reason <why>`.

## Sign the host's browser tab in

For a different user, or a journey Torque has no flow for, drive the host's browser tab and sign
it in yourself. Do not ask the consultant to sign in to an org the CLI is authorized for.

1. Run `torque browser login --target-org <org-alias> --workspace <private-path> --client <client-name>`
   in the background. It prints one local address and waits.
2. Navigate the tab to that address with the host's browser tool. Never fetch it with curl or a
   web fetch tool. The command then names the org, sandbox or production, and the user, and exits.
3. Wait until the tab shows the org (an action sent while it still redirects can fail), then
   confirm the signed-in user in the page before testing: the "Logged in as" banner when you
   signed in through Log in as, the User Id otherwise.
4. Go to the record or page from there.

A login page after step 2 means the sign-in failed: retry once, then stop and report. Never type
or ask for a password. A "Change Your Password" page means the sign-in worked and that user's
password has expired: stop and tell the consultant, who changes it; the CLI and the API keep
working. A sign-in replaces that browser profile's session for the org, so every tab on that org
becomes that user: say so before signing in as a test user. After a deploy, sign in again before
judging a page. Do not read the tab's network log during sign-in. Keep one driver per browser
session, leave unrelated tabs alone, and treat browser journeys as able to write records:
establish side effects and cleanup within task scope.

To test as another user, see `/qa-multiprofile` (`--as-alias`, `--as-user`). For a scripted
Playwright browser, `torque browser login --target-org <org-alias> --storage-state <path>` writes a
session file (keep it out of repositories; `torque browser logout --storage-state <path>` deletes
it). In a connected workspace neither is available: use `torque browser browser <flow-name>` under
a granted browser window. `torque doctor --for browser-login` says whether this machine can sign a
tab in.

## Torque's packaged journeys

```sh
torque browser browser <flow-name> --target-org <org-alias> --workspace <private-path> --client <client-name>
```

Run only a journey the installed help lists; client journeys live in the client's private
configuration. Add `--headed` when helpful, except under a browser window a delegated approver
granted: there Torque's browser runs headless only and `--headed` is refused. This route signs in
as the CLI's admin user: label its results admin-only.

## Evidence and cleanup

Test records: save each created record's Id when it is created, in the private task directory,
and delete only those Ids (an Id-file delete such as `sf data delete bulk --file ids.csv` with an
Id column), never records matched by name, title, creator or date; if the Id list is lost or
incomplete, stop and ask instead of matching.

Save screenshots and the manifest privately, tell real UI events from backend calls, read back
the records the journey changed, and report the user, the route, missing coverage and any cleanup
survivors.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
