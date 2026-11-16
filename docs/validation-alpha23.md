# Validation for alpha 23

Alpha 23 is a fix release on top of alpha 22: the gate reads an unknown variable in a path as every value a shell
can give it and a Windows share or device path as the drive path it names, a live Antigravity CLI 1.5.9 session called the gate hook for the first time, and the advisory
catalogue's text is ASCII. The changelog lists each change. This record separates what was run live, what the tests
cover, and what was not run.

## Observed results

Run live on Windows 11 with Antigravity CLI 1.5.9 (personal account, made-up workspaces and a made-up client, no
Salesforce org):

| What | Result |
|---|---|
| Build-only mode | The session called the gate hook; it refused a read of a client file, a write into a client folder and an org query. The session loaded the build-only rule and opened a practice skill the always-loaded block names |
| Full mode | The gate refused a write to a client's consent record |
| Hook input | Antigravity 1.5.9 sends keys alpha 22's tests did not have (`cwd`, `read_file` line ranges, `write_file` `Overwrite`, `run_command` timing fields); the gate decided each as before, and `tests/test_gate_antigravity.py` now replays them |
| Command output | The catalogue's long dashes reached the session as replacement characters; the catalogue is now ASCII |

A second gate fix came from probing Windows path forms on 2026-11-15: in build-only mode a client file could be
read through an administrative share (`//localhost/c$/...`, `\\127.0.0.1\c$\...`, the machine's own name) or a
device path (`//?/C:/...`, `\\?\UNC\...`, a volume name), through Bash, PowerShell and the Read tool; full mode
already kept a record named that way. After the fix each form is refused, a share that does not exist on the
machine is not, and a network path is no longer looked up (a probe of an unknown host took 1.2 s before, 0 after).
`tests/test_gate_windows_share_paths.py` holds 19 cases; 17 of them fail on the gate before this fix (the two that
pass there are ordinary work through a share and full mode's record check).

The first gate fix came from that check and from an independent review of the first version of the fix, which found more
shapes and some regressions. `tests/test_gate_unknown_expansions.py` holds 32 cases; 21 of them fail on the alpha 22
gate. On Windows with Python 3.13 the three changed test files passed (122), and the full gate test set passed on the
first version of the fix (5,790 passed, 80 skipped). CI on this release's tree runs the whole suite on Linux, macOS
and Windows with Python 3.10, 3.12 and 3.14 (pull request 54).

## Remaining acceptance

Not verified in this release:

- Under Antigravity: connected mode, `edit_file`, connector and browser tools, worker roles, and macOS or Linux.
- Everything alpha 22 lists as not verified, except the Antigravity hook call above.

Known limits:

- An unknown expansion whose value the gate cannot model (a loop variable, `set --`, `${X#x}`, an array, `read`,
  `printf -v`) is listed in [AI access](ai-access.md) under what the gate cannot stop.
- A word with more than 64 variable expansions is refused in build-only mode; put the text in a file.
- On Windows, a share on another machine that reaches this one and a share or host name built from a variable are
  not covered; a drive mapped with `net use` or `subst` and a DFS namespace were not tested. No test created a real
  share (that needs elevation): the share lookup was tested with a stand-in for the registry.
- The known limits listed for alpha 22 remain, except where the changelog says otherwise. Whether the macOS timing
  overrun is gone is read from this release's CI runs.

No package-index release and no demo readiness is claimed.
