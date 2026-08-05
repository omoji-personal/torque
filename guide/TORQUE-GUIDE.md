# Torque — the guide

**The guide is [`Torque-Guide.pdf`](Torque-Guide.pdf)** (21 pages), built from
[`torque-guide.html`](torque-guide.html) with `node guide/build-pdf.mjs`.

This file used to be a second, older guide. It said the same things in different words, and then
it stopped saying the same things — it still described the agent as unable to write to production,
when in fact a production write works like any other write once the operator approves it. A
document that contradicts the shipped one is worse than no document, so this is now a pointer.

## What's in the PDF

| § | |
|---|---|
| 01 | What it actually does — deploy-and-verify, mass-update with undo, read-only audit, Apex under review, browser verification, session logs |
| 02 | Why this isn't the MCP server — compared against the Salesforce MCP, Gearset/Copado and sfdx-hardis, including the rows Torque loses |
| 03 | Setup — clone to working, in about ten minutes |
| 04 | The operations, worked through |
| 05 | Working in production, on your authority — the five layers, the threat model, and where the boundary honestly sits |
| 06 | When it refuses — troubleshooting |
| 07 | Proving it — the validation harness |
| 08 | Where it came from |
| 09 | Reference — commands and layout |

## The security posture

For the threat model — what the gates bind, what no PreToolUse hook can bind, and what carries the
weight instead — see **§05** of the PDF. To report something that defeats a stated invariant, see
[`../SECURITY.md`](../SECURITY.md).
