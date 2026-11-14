# Browser sign-in

`torque browser login` sends a browser to an org the Salesforce CLI is already authorized for,
through the Salesforce front door: no password is typed, and the Salesforce sign-in address is
never printed, logged, written to disk or put in an error message. It is not available in a
connected workspace (exit 6) or a build-only one.

**What the redirect proves.** Exit 0 in host-tab mode means the browser tab was sent to the
org's single-use sign-in address for the alias's user. It does not prove that the page loaded,
that Lightning is usable, or what another user can see. Wait for the org's page and confirm
the signed-in user in it before testing.

**When the consultant is needed.** The command cannot fix these; it stops or the tab shows
them, and the consultant acts outside the agent:

- an expired password: the tab shows "Change Your Password" (the CLI and API keep working);
- a revoked or expired CLI authorization: exit 3, `sf org login web --alias ALIAS`;
- a user with no login access for `--as-user`: the org's Log in as policy, or the user's grant;
- device verification or a second factor that the front door does not satisfy.

Status: one live host-tab run (Claude Code with the Chrome extension, Windows) sent a tab into
a practice org without printing an address; everything else is offline tests. The
[checklist](#what-a-live-run-still-has-to-prove) at the end says what a live run must still show.

## Why a command for this

The Salesforce CLI can produce a sign-in address for an org it is authorized for
(`sf org open --url-only`). That address signs in whoever opens it, once, for about a minute.
Whatever a command prints reaches the AI session, so the rules tell an agent never to run that
command. An agent that drives a browser tab therefore had no way to sign the tab in, and asked
the consultant to do it by hand.

`torque browser login` closes that gap. The session only ever sees a local address.

## Two modes

| Mode | For | Needs | Check |
| --- | --- | --- | --- |
| Host tab (default) | An agent driving a browser tab with its host's browser tool | The Salesforce CLI | `torque doctor --for browser-login` |
| Session file (`--storage-state PATH`) | A scripted Playwright browser | The Salesforce CLI, the `browser` extra and a Playwright browser | `torque doctor --for browser` |

## Host tab

```sh
torque browser login --target-org ALIAS --workspace W --client NAME
```

1. The command checks the alias with the Organization query, compares the org with the
   client's own records, starts a listener on `127.0.0.1` and prints one line: a local
   address such as `http://127.0.0.1:52344/Zk3v...`. It then waits. Run it in the background.
2. Navigate the browser tab to that address with the host's browser tool. Do not fetch it
   with curl or a web fetch tool: only a browser navigation is answered.
3. At that moment the command asks the CLI for the single-use address and answers the tab
   with a redirect to it. The command prints that the tab was sent (event `sent`,
   `session_verified: false`), the org ID, whether the org is a sandbox or production, its
   edition, the username and the User Id, and exits 0. It cannot see the tab.
4. Wait until the tab shows the org's page, then check which user it shows before testing. In
   Lightning, `$A.get('$SObjectType.CurrentUser.Id')` in the tab's JavaScript tool returns the
   User Id to compare. A login page means the sign-in failed (retry once, then stop and
   report); "Change Your Password" means the user's password expired, and device verification
   means the same: the consultant acts.

Options: `--path /lightning/...` lands on a page (the default is the org's own landing page;
going to a record after the sign-in is the reliable order), `--timeout SECONDS` (default 180),
`--api-version N` when the CLI's cached API version is wrong after a sandbox refresh,
`--json` for one JSON object per line, `--any-client` for a browser that sends no fetch
metadata.

With `--json` the first line is
`{"event": "waiting", "address": "...", "timeout_seconds": 180, "expires_at": "..."}` and the
second `{"event": "sent", "session_verified": false, "org_id": "...", "org_kind": "sandbox",
"org_edition": "...", "username": "...", "user_id": "...", "org_check": "...", "check_in_page": "..."}`
(`check_in_page` is the next step above), or `{"event": "failed", "exit_code": N, "reason": "..."}`.
The org ID, username, User Id and edition are the CLI's; one that does not look like what it
should be (an Id of 15 or 18 characters, a username, an edition name) is printed as
`(unreadable)`, in the JSON and in the text. Everything printed also passes the package's
redaction; the local address comes through unchanged.

A login page after the redirect means the sign-in failed. Retry once, then stop and report.
Never type or ask for a password.

A sign-in replaces that browser profile's session for the org: every tab on that org becomes
that user. Say so before signing in as a test user in a browser the consultant also works in.

## The org check

Before anything is printed, the org the alias points to is read with
`SELECT Id, IsSandbox, OrganizationType FROM Organization` (the query the rules prescribe;
`sf org display` is never run). It is compared with what the selected client already records
for that alias:

- the consent record (`consent.json`), when it lists the alias;
- the test-user seed (`config/test-users.json`), when it is written for the alias.

A different org ID stops the command (exit 5) and nothing is signed in. When neither record
names an org ID for the alias, the command goes on and says
`org not checked against the client record`. No new record is created. The org ID in the CLI's
answer at the moment of the redirect is compared again, so an alias that was pointed elsewhere
in between is not signed in.

## As another user

**An alias for the test user (preferred).** Authorize the Salesforce CLI as the test user
once, by hand, outside the agent: `sf org login web --alias ALIAS-OF-USER` against the same
org. Then:

```sh
torque browser login --target-org ALIAS --as-alias ALIAS-OF-USER --workspace W --client NAME
```

The command checks that both aliases are the same org (org ID) and reports the username it
signs in as. This is the user's own session: no banner, no org-wide setting.
`--as-alias` also takes a role name from the test-user seed that has a `cli_alias`; the
username is then compared with the seed's before the redirect.

**Log in as (fallback).** For a user who cannot be authorized in the CLI:

```sh
torque browser login --target-org ALIAS --as-user USERNAME_OR_ID --workspace W --client NAME
```

The admin's tab lands on that user's page in Setup and the command prints the next steps:
click Login, confirm the "Logged in as" banner, and when done do not click "Log out as" (it
can end the whole session): close the tab or sign in again. This needs the org's
"Administrators Can Log in as Any User" policy (Setup, Login Access Policies), or login access
granted by the user, and Modify All Data for the admin. `--direct` sends the tab straight to
Salesforce's Login As address instead of the Setup page; that route is unproven until a live
run confirms it, and its output says so.

**Who cannot be signed in.** A user with the API Only User permission cannot have a browser
session: Salesforce refuses the sign-in address for such a user. Use a separate user for
browser tests. Inactive users cannot be logged in as. Experience Cloud site users are outside
both routes.

## Session file for a scripted browser

```sh
torque browser login --target-org ALIAS --storage-state PATH --workspace W --client NAME
torque browser logout --storage-state PATH
```

Torque's own headless browser signs in through the same path its browser tests use, the
page's user is read and compared with the alias's user, and the session is written to `PATH`
in Playwright's storage-state format. A script then starts with
`browser.new_context(storage_state=PATH)`.

- The file signs a browser in as that user until the session ends. It is created readable by
  its owner only (mode 600; on Windows an access list for the user, SYSTEM and Administrators).
- A path inside a git work tree is refused unless git ignores it. A Torque workspace ignores
  `clients/`, so `clients/NAME/state/browser-auth/ALIAS.json` works.
- The command prints the path, never the content.
- `logout` deletes the file, and only a file that is such a session file.
- `--as-alias` works here too. `--as-user` does not: use an alias for the test user.
- Playwright's debug settings `DEBUG`, `DEBUG_FILE` and `PWDEBUG` print the address the browser
  is sent to, or show it in a visible inspector. While any of them is set, this mode and every
  Torque browser run that signs in through the front door (`torque browser browser`,
  `multiprofile`) refuse to start, in every workspace mode, and name the variable to unset.
  Only a run attached to a browser you signed in yourself (`TORQUE_BROWSER_CDP`, outside
  connected mode) keeps them: it is sent to the org's own address, never to a sign-in address.
  To debug a journey, write a session file first and debug a script that loads it.
- Without Playwright or its browser the mode stops with exit 8 and says what to install.
  Host-tab mode needs neither.

## Workspace modes

| Mode | Behaviour |
| --- | --- |
| `full` (default) | Works. The gate does not block it. |
| build-only | The gate blocks every `torque browser` call, this one included, with its usual message. |
| connected | Refused (exit 6). Connected mode allows one browser, Torque's own guarded one, and refuses host browser tabs because their org cannot be checked. A sign-in for a host tab, or a session file for another browser, would step outside that guard. Use `torque browser browser FLOW --target-org ALIAS` under a granted browser window. |

## Exit codes

| Code | Meaning | What to do |
| --- | --- | --- |
| 0 | Host tab: the tab was sent to the org's sign-in address, not yet confirmed. Session file: written after the page's user was checked | Check the page and its user |
| 1 | No browser navigation reached the local address before its expiry, or the CLI answered after it | Run it again and navigate the tab sooner |
| 2 | Options that cannot be used | Read the message |
| 3 | The alias has no CLI authorization, or it expired | The consultant runs `sf org login web --alias ALIAS` in their own terminal |
| 4 | The Salesforce CLI is not installed | Install it |
| 5 | Another org or user than recorded, or than the other alias | Check the alias with the Organization query, or correct the record |
| 6 | Refused by the workspace's mode | See the table above |
| 7 | No local port could be opened | Free local ports, run again |
| 8 | Playwright or its browser is missing (`--storage-state`) | Install the extra and `python -m playwright install chromium` |
| 9 | The sign-in itself failed | Read the message; the CLI's own text is shown only redacted |

## What protects the local address

- The listener binds `127.0.0.1` only, on a port the system picks, exclusively (on Windows a
  second process cannot bind the same port).
- The path is a 32-character random code made by the command. A wrong path gets a 404 and
  nothing else; twenty wrong requests close the listener.
- Only a top-level browser navigation is answered. A fetch, a prefetch, an image request,
  curl or a script gets a refusal and no Salesforce address is asked for.
- The `Host` header must be the loopback address.
- One valid request, then the listener closes.
- The printed expiry (`expires_at`, `--timeout` seconds after the address is printed) is one
  absolute deadline. After it no Salesforce address is asked for and none is sent, also for a
  request that began before it. A connection is read for at most five seconds in all and never
  past the deadline, however slowly its bytes arrive, so a client that connects and waits cannot
  hold the listener. When the CLI answers only after the deadline, its address is dropped unsent
  (it is single-use and expires by itself) and the command exits 1 saying so.
- Nothing about a request is logged.

Any process on the machine can reach a loopback port, whoever runs it, so the code is the
secret. A process of the same account can read it from the session's output, but such a
process can already ask the Salesforce CLI for an address itself: the relay adds no new
power. It keeps the address out of the session in normal use.

The browser and the command must run on the same machine. For a browser on another machine,
forward the port; never bind another interface.

## Replay scripts

`torque browser sanitize-replay SCRIPT` rejects a recorded replay that holds a sign-in
address or a session value (`frontdoor.jsp?otp=`, `frontdoor.jsp?sid=`,
`services/oauth2/singleaccess`, tokens, cookies, authorization headers). It scans the text as
written and after each of up to four rounds of percent-decoding; text still encoded after that
is rejected too. It never prints the matched value.

It is a scan of the script's text for known shapes. It cannot prove that code which builds or
decodes an address at run time (another encoding, a computed string, a value read from a file)
holds none, so a CLEAN result is not a full secret audit. A replay signs in at run time instead:
`torque browser login --storage-state PATH` and `browser.new_context(storage_state=PATH)`.

## Test users: the seed file

`clients/NAME/config/test-users.json` names the users browser tests run as. It holds no
password, token or session value; such field names and secret-shaped values are refused. On
macOS and Linux the file must be mode 600 and owned by you.

```json
{
  "schema_version": 1,
  "alias": "acme-sbx",
  "org_id_18": "00D000000000001AAA",
  "users": {
    "inside_sales": {
      "username": "inside.sales@acme.example",
      "user_id": "005000000000002AAA",
      "cli_alias": "acme-sbx-inside-sales",
      "expected_profile": "Standard User",
      "license": "Salesforce",
      "expected_permission_sets": ["Sales_Console"],
      "allowed_in_prod": false
    }
  }
}
```

| Field | Required | Used today |
| --- | --- | --- |
| `schema_version` | yes, `1` | Checked |
| `alias` | yes | Must equal `--target-org`; one seed file serves one org alias |
| `org_id_18` | yes | Compared with the org by `torque browser login` |
| `users` | yes | Each key is a role name of your choosing |
| `username` | yes | Compared with the alias's user when `--as-alias ROLE` is used |
| `user_id` | yes | The user `torque browser multiprofile` logs in as and checks in the page |
| `cli_alias` | no | The alias `torque browser login --as-alias ROLE` signs in with |
| `expected_profile`, `license`, `expected_permission_sets`, `allowed_in_prod` | yes | Checked for shape only. The live check that reads them (`validate_user_via_soql`) is not called by a run yet |

`torque browser multiprofile` still reaches each user through Log in as; it does not use
`cli_alias` yet.

## What a live run still has to prove

Once on a sandbox, and the host-tab part once per host (Claude Code, Codex, Antigravity):

1. `torque browser login --target-org ALIAS ...` prints one local address; navigating the
   host's browser tab to it lands in the org; the command names the right org, kind, username
   and User Id.
2. The session transcript contains no `frontdoor.jsp`, no `otp=` and no org sign-in address.
3. The host's browser sends fetch metadata to `http://127.0.0.1` (the sign-in works without
   `--any-client`), and the host's tab listing does not show the address during the redirect.
4. A second navigation to the same local address fails; curl to a fresh address gets a
   refusal and the tab can still use it.
5. `--path /lightning/setup/SetupOneHome/home` lands there; note any "Invalid Page
   Redirection" with the path that caused it.
6. `--as-alias` with a test user's alias signs in as that user; with an alias of another org
   it stops with exit 5.
7. `--as-user USERNAME` lands on the user's Setup page; Login works there; the banner shows.
   Then `--as-user USERNAME --direct`: does the tab arrive logged in as the user?
8. An alias with an expired authorization stops with exit 3 and the login instruction.
9. `--storage-state` with Playwright installed writes the file; a script that loads it is
   signed in; `logout` deletes it. On Windows, check the file's access list.
10. In an org that requires MFA or a passkey for the UI: does the tab still land signed in?
