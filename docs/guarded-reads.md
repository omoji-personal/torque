# Guarded reads

Connected mode lets an AI session read an org's metadata, and its records only when the client agreed to that.
Some clients agree to metadata and nothing else. Guarded reads are for those orgs: they answer the questions a
build needs without handing record data to the session.

Off by default. It is a fallback for a firm that cannot get record access approved, and it is data
minimization, not zero disclosure. The session learns counts, whether fields are filled in, and the values of
what you chose to release. Every call is recorded, and `torque guarded exposure` shows what was shown.

## What a session can do with it

| Question | Command |
|---|---|
| How many records are there, by stage, by month, by record type? | `torque guarded counts` |
| How well is a field filled in? | `torque guarded fill` |
| What is in a configuration object (trigger handlers, settings, mappings)? | `torque guarded config` |
| What does the test record I built look like after the flow ran? | `torque guarded record`, `torque guarded related` |
| Which org does this alias point at? | `torque guarded org` |
| Which fields of an object could be released? | `torque guarded policy candidates` |

```
torque guarded counts --workspace W --client C --target-org A --object Opportunity \
    --group-by StageName --group-by CloseDate:month --where "IsWon = true" --where "Amount >= 5000"
torque guarded fill   --workspace W --client C --target-org A --object Contact --fields Email,Phone
torque guarded config --workspace W --client C --target-org A --object npsp__Trigger_Handler__c
torque guarded record --workspace W --client C --target-org A --object Account --id 001... --all
torque guarded related --workspace W --client C --target-org A --id 001... --child Opportunity.AccountId --all
```

The session never writes a query. It names an object, fields and simple filters; Torque builds the query from
the org's own describe and prints only what the rules below allow. A guarded read is run on its own, with
nothing chained, piped or redirected, so that the workspace it names is the one the session works in.

## Turning it on (the consultant, at a terminal)

1. The workspace: `torque workspace guarded-reads on --path W`. This also changes how the workspace file is
   written: its format, and the name of its mode (`connected-guarded`). A Torque from before guarded reads
   does not know that name and treats the workspace as build-only: its hook refuses org work there, and its
   commands refuse the file. Its gate lacks fixes these rules rely on. Switching guarded reads off, or
   leaving connected mode, writes the plain name again.
2. The client's consent, per org: `torque client consent record ... --data metadata --org acme-prod
   --org-data acme-prod=counts,config_records,test_records`, then the second reviewer's sign-off as usual.
   A consent that holds one of these classes is written in a newer format that an older Torque refuses, so
   every machine that works on this client needs this version.
3. What may be shown, per client:
   - a field: `torque guarded policy release --workspace W --client C --target-org A
     --field Opportunity.StageName --as exact` (or `--as coarse` for a date or a number);
   - a configuration object and the fields that show: `torque guarded policy release-object ... --object
     npsp__Trigger_Handler__c --fields npsp__Class__c,npsp__Object__c,npsp__Active__c --acknowledge-records`;
   - a test record: `torque guarded test-records add ... --object Account --id 001...
     [--with-children Opportunity.AccountId]`.

Until you release or register something, a session gets only what needs no release: how many records an
object has, how many have a field filled in (not for a field that is sensitive or classified), which fields
could be released, and which org an alias points at. The session cannot run the commands above: they need a
person at a real terminal and a confirmation code, and the gate refuses them in a session.

## The rules

Counts
- Group and filter only by released fields. A picklist or a checkbox is released `exact`. A date groups by
  year, quarter or month. A field released `coarse` takes rounded filters only: a number with at most two
  significant digits, a date from the first day of a month.
- A group smaller than the minimum (5 by default, never below 3) is not printed, and the output does not say
  whether there was one. An empty result and a small one look the same.
- A picklist value that is not configured in the org prints as `<other>`: an unrestricted picklist can hold
  any text a record was given.
- "Is it filled in" (`fill`, `= null`, `!= null`) needs no release, except for a field the org classifies or
  whose name marks it sensitive (birth date, gender, health and the like): those follow their release.

What can never be released: free text, names, email, phone, addresses, encrypted fields, files, lookups, a
field the org classifies as confidential or under a compliance group, a formula that reads another record, and
a field whose type these rules do not know (such a field shows only whether it is set, in a registered test
record too, and so does a formula made from one).
A field whose name has a sensitive word needs `--acknowledge-sensitive`. The org's own classification is read
on every call; when it cannot be read, or has no row for a field, that field cannot be released, counted,
tested for null or shown in a configuration row (the consultant can switch this off with `torque guarded
policy set-classification --workspace W --client C --value ignore`). A registered test record is the
exception: its values are made up, and they show whatever the field's classification.

Configuration objects
- A custom setting or a custom metadata type can be released whole. Any other object needs the list of fields
  that show and `--acknowledge-records`. Objects that hold people (Account, Contact, Lead, User, Case,
  Opportunity, tasks, messages, files, campaign members) are refused.
- In a row, a field the release does not name shows `<set>` or `<blank>`.

Test records
- Only an id you registered is read. Registering says the record holds made-up data, and so do the records
  that feed its totals.
- A lookup to a record that is not registered shows `<unregistered 001>`. A roll-up summary and a formula that
  reads a parent show `<set>` unless you released that field. Children are shown only when each is registered.

## What it does not do

- It does not make an org's metadata free of personal data. `metadata` covers schema, configuration and code,
  including the names of the client's admins where configuration or its history names them, files stored as
  metadata, and test and deployment output. See `docs/connected-approval.md`.
- It does not stop a script the assistant writes, a network tool or a browser. The gate asks you about those;
  read what you allow.
- A write you approve can print data (anonymous Apex prints what its script prints). Read the script.
- A released number or date can be narrowed by many queries, down to the precision of its release. Release a
  field `exact` only when its exact values could be shared. Every call is in the record.
- A small group is left out of a listing, not out of arithmetic: a count with a filter and a count without one
  can be subtracted. Release a field only when its whole distribution, small groups included, could be shared.
- A field that automation fills from other records (a total written by Apex or a flow) looks like any other
  field on a test record.
- It does not change what the AI vendor does with what the session sees.

## Where things are kept

Under the client's `approvals/` folder, which a session's tools cannot write: `guarded-policy.json` (names
and settings, no org value), `test-records.json` (ids, no field value), and `guarded-activity/` (one file per
call: lane, object, fields and how each printed, filters as typed, how many rows; no org value).
