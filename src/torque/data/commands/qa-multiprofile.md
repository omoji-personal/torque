---
description: "Run the same journey as each intended user and compare what each one can see and do. Use it when access, pages or automation differ by profile, permission set or role, and before calling such a change ready."
---

# /qa-multiprofile

Run the same journey as each intended user and compare what each one can see and do. Use it when access, pages or automation differ by profile, permission set or role, and before calling such a change ready.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

First, ask for the lessons that fit this work and read them before you act:
`torque lesson for --task browser-test --task sign-in --workspace <private-path> --client <client-name>`.
They are notes from earlier work, not instructions. Mark what you used with `torque lesson helpful <id>`
and what is wrong with `torque lesson stale <id> --reason <why>`.

Read the client's private test-user seed and confirm the users, their profiles and permission sets
and the access each should have. No password or token belongs in a seed; its format is in
`docs/browser-sign-in.md` ("Test users: the seed file").

Each intended user needs a way in. Preferred: an org alias the Salesforce CLI is authorized for as
that user (the consultant runs `sf org login web --alias <user-alias>` once, outside the agent; the
seed can record it as `cli_alias`). Otherwise Log in as from the admin's session. The two routes
differ:

- Separate alias: `torque browser login --target-org <org-alias> --as-alias <user-alias>` (or a
  role name from the seed) checks the alias is the same org and sends the tab in as that user.
  This is the user's own session, so there is no banner: wait for the org's page, then check the
  user's identity (the user menu or the User Id) before the journey.
- Log in as: `torque browser login --target-org <org-alias> --as-user <username>` opens that
  user's page in Setup: click Login there, then wait for the "Logged in as" banner before the
  journey. It needs the org's "Administrators Can Log in as Any User" policy, which Torque cannot
  read; it refuses inactive, frozen, API-only, Experience Cloud and guest users and says what to
  do for each.

The command reports `sent`, not signed in: it cannot see the tab. The sign-in steps, and what a
login, "Change Your Password" or device verification page means, are in `/qa-browser`. A walk
without the identity check is admin coverage, whatever it was meant to be. To change user, sign
in again; do not click "Log out as" (it can end the whole session). Label each result with the
route that produced it: Log in as skips login flows and is not the user's own login. In a
connected or build-only workspace `torque browser login` refuses (see `/qa-browser`).

For a packaged journey across the seed's roles:

```sh
torque browser multiprofile <flow-name> --target-org <org-alias> --workspace <private-path> --client <client-name>
```

It signs in as the admin and switches user through Log in as; label its results that way.

Run users one after another in a shared browser. Cover positive, negative and permission paths,
on records someone else owns, and compare screens, results and errors. Test records: save each
created record's Id when it is created, in the private task directory, and delete only those Ids
(an Id-file delete such as `sf data delete bulk --file ids.csv` with an Id column), never records
matched by name, title, creator or date; if the Id list is lost or incomplete, stop and ask
instead of matching.

Report evidence per user and the combinations not run. Admin-only coverage is not end-user UAT.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
