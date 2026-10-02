# Validation for alpha 19

Alpha 19 adds internal initiatives beside clients: the same sessions, change records,
context and handoffs, with client-only powers (org, consent, approvals, connected mode,
verify-deploy) refused for initiatives. It adds a maintenance flag that pauses every
record and configuration writer, and extends the gate's protected-record check to
initiative bindings and client control, request and claim folders in every mode.
Client commands and records are unchanged.

## Observed results

macOS with Python 3.14.

The full suite passed 3,550 tests and 154 subtests with 10 skips (excluding one
pre-existing package self-test file that exits at import). The offline harness, both
workflow sync checks and the provenance check pass; one adapted package file's
destination hash is updated and its original source hash retained. The wheel and
source distribution pass the distribution checker. A clean wheel installation passes
dependency checks and the installed CLI smoke suite.

## Remaining acceptance

Tests run under one account on macOS. Multi-account behavior on a shared Linux host,
group permissions and the admin provisioning scripts are later releases. Known gate
gaps that predate this release remain open and are tracked for a follow-up:
commands wrapped in `bash -c`, `sh -c` or a quoted `eval`; substitutions containing
separators; parameter defaults that hide a path; `xargs`; `git clean` on an ignored
`clients/`; and a quoted separator followed by a comment. With the gate hook on,
read-only commands that name a whole initiative folder are refused until a later
release narrows the check. No live org command ran in these checks, and no
package-index release or demo readiness is claimed.

## Review process

The design converged after five rounds of adversarial review by four models before
implementation. Each of the nine plan tasks passed a spec and quality review; the
gate task passed five rounds of probe-based security review, and a whole-branch
review preceded one final fix wave. These reviews supplement the measured results
and do not establish the remaining live or multi-account acceptance.
