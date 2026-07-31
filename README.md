# Torque

**An AI operations workspace for any Salesforce org — built on Claude Code, governed by
deterministic gates, proven by a live validation harness.**

Salesforce gave AI agents the tools. Torque is the discipline for letting one operate:
**the agent cannot write to production on its own** — a production write requires a
deliberate, operator-present override it provably cannot mint — destructive operations
require an operator-present approval token, and every capability is exercised against a real
org before it counts as done, with the receipts rendered from machine-readable attestations.

The safety layer was driven to convergence by a nine-round multi-model plan audit, then the
built gates through four independent adversarial rounds plus a standing self red-team — 128
runnable attack fixtures, ten self-test mutators that must fail when a guard is neutered, and
a full capability run (real deploy → verify → purge, mass-update → undo, live browser render)
green on a Developer Edition org. The gate reasons about what a glob or `$variable` *could*
expand to, not just its literal text, and fails closed on any indirection it cannot resolve.
The honest residual — arbitrary same-uid code, disclosed by the OS — is stated plainly in the
guide, not papered over.

> Reproduce: `python3 harness/validate.py --profile release --target-org <your-DE-org>`
> Evidence and the full audit trail: `harness/VALIDATION.md`.

*What Torque is not:* a CI/CD pipeline (Gearset/Copado's category), an in-org codegen IDE
(Agentforce Vibes'), or a command library (sfdx-hardis — respected prior art). It is the
operator-grade safety + validation layer those categories don't ship.

Setup, the safety model, and the honest threat model: `guide/TORQUE-GUIDE.md`.
