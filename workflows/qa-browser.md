# /qa-browser

Test a page or a journey in a browser as a named user, sent in through the Salesforce CLI's existing authorization, and check what it changed. Use it to see a change as its user sees it, or to run one of Torque's packaged browser journeys.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

{{lessons --task browser-test --task sign-in}}

## Sign the host's browser tab in

For a different user, or a journey Torque has no flow for, drive the host's browser tab and sign
it in yourself. Do not ask the consultant to sign in to an org the CLI is authorized for.

1. Run `torque browser login --target-org <org-alias> --workspace <private-path> --client <client-name>`
   in the background. It prints one local address and waits.
2. Navigate the tab to that address with the host's browser tool, as printed. Never fetch it with
   curl or a web fetch tool. The command then says the tab was sent (event `sent`,
   `session_verified: false`), names the org, its edition and the user, and exits. It cannot see
   the tab, so nothing is signed in until you have looked.
3. Wait until the tab shows the org's page (an action sent while it still redirects can fail),
   then check which user it shows before testing: the User Id or the user menu for a sign-in as
   the alias's own user; the "Logged in as" banner after Log in as.
4. Go to the record or page from there.

A login page after step 2 means the sign-in failed: retry once, then stop and report. Never type
or ask for a password. A "Change Your Password" page or a device verification page needs the
consultant: stop and tell them (an expired password is changed by the consultant; the CLI and the
API keep working). A sign-in replaces that browser profile's session for the org, so every tab on that org
becomes that user: say so before signing in as a test user. After a deploy, sign in again before
judging a page. Do not read the tab's network log during sign-in. Keep one driver per browser
session, leave unrelated tabs alone, and treat browser journeys as able to write records:
establish side effects and cleanup within task scope.

To test as another user, see `/qa-multiprofile` (`--as-alias`, `--as-user`). For a scripted
Playwright browser, `torque browser login --target-org <org-alias> --storage-state <path>` writes a
session file (keep it out of repositories; `torque browser logout --storage-state <path>` deletes
it). In a connected or build-only workspace neither is available (`torque browser login` exits 6):
in a connected workspace use `torque browser browser <flow-name>` under a granted browser window;
in a build-only workspace the consultant runs the browser test and you prepare the steps and the
expected results. `torque doctor --for browser-login` says whether this machine can send a tab in.
`DEBUG`, `DEBUG_FILE` and `PWDEBUG` stop every Torque browser run that signs in: unset them, or
debug a script that loads a `--storage-state` file.

## Torque's packaged journeys

```sh
torque browser browser <flow-name> --target-org <org-alias> --workspace <private-path> --client <client-name>
```

Run only a journey the installed help lists; client journeys live in the client's private
configuration. Add `--headed` when helpful, except under a browser window a delegated approver
granted: there Torque's browser runs headless only and `--headed` is refused. This route signs in
as the CLI's admin user: label its results admin-only.

## Evidence and cleanup

Test records: {{test-records}}

Save screenshots and the manifest privately, tell real UI events from backend calls, read back
the records the journey changed, and report the user, the route, missing coverage and any cleanup
survivors.
