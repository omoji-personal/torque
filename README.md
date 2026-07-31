# Torque

**An AI operations workspace for any Salesforce org — built on Claude Code, governed by
deterministic gates, proven by a live validation harness.**

Salesforce gave AI agents the tools. Torque is the discipline for letting one operate:
production is ineligible for writes *by construction*, destructive operations require
operator-present approval an agent provably cannot mint, and every capability is
exercised against a real org before it counts as done — with the receipts rendered from
machine-readable attestations.

> Status: under active build. Phase status and validation evidence: `harness/VALIDATION.md`.

*What Torque is not:* a CI/CD pipeline (Gearset/Copado's category), an in-org codegen IDE
(Agentforce Vibes'), or a command library (sfdx-hardis — respected prior art). It is the
operator-grade safety + validation layer those categories don't ship.

Setup, the safety model, and the full guide: `guide/TORQUE-GUIDE.md` (P5).
