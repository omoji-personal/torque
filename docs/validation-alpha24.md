# Validation for alpha 24

Alpha 24 changes how the 54 recipes are written and listed, not what they say: every recipe copy is rendered from
one source with one drift check, shared text is written once, the command reference moved into each command's
`--help`, and `torque workflows list` prints a short use-when line per recipe. Recipe names, catalogue descriptions
and interface lines are unchanged. The changelog lists each change. This record separates what was run, what the
tests cover, and what was not run.

## Observed results

| What | Result |
|---|---|
| Drift check | `scripts/sync-workflows.py --check` passes on this tree (210 files); tests show it fails on a copy edited by hand, a missing copy, a stray copy, a recipe without a catalogue entry and the reverse, and a changed heading, description or interface line |
| Rendered recipes | The lesson preamble renders byte for byte as in alpha 23; the test-record rule is the same words on one line in 8 recipes; rendered recipes total 137,695 characters, from 141,422 |
| Help text | `tests/test_recipe_help.py` checks each moved fact in the real help output; each was read in the code first. One recipe claim was wrong and is gone: `torque context --json` still follows `--limit`, `--since` and `--all` |
| Recipe list | `torque workflows list`: 66 lines and 6,060 characters, from 67 and 11,742; `--json` keeps every field and adds `use_when` |
| Package provenance | `scripts/check-provenance.py` passes (169 files) after the eight changed package `cli.py` files |

On Windows 11, the full offline suite at the last code commit: 9,599 passed, 861 skipped, 1 failed; the 12 fixture
suites passed. The failure (`test_side_eff_evidence.py::test_a_qa_run_on_an_org_without_debug_logs_is_incomplete_not_failed`)
fails the same way on alpha 23 on that machine: the fake `sf.exe` the test writes cannot be started there. CI on
this release's tree runs the whole suite, the drift check, the provenance check and the distribution checks on
Linux, macOS and Windows with Python 3.10, 3.12 and 3.14 (pull request 56).

## Remaining acceptance

Not verified in this release:

- Routing with the use-when lines: no host was run against the new list. The behaviour test before this change
  (today's recipes, 10 of 10 tickets routed right) used the full descriptions the list showed then.
- Whether an agent reads a command's `--help` when a recipe points to it.
- A workspace upgrade of the changed recipe copies on a real workspace (unedited copies are replaced, edited ones
  kept, as in every release).
- Everything alpha 23 lists as not verified.

No package-index release and no demo readiness is claimed.
