# Production approval (connected workspaces)

This workspace is in connected mode: org writes need a per-write approval from
the consultant. For production orgs this rule overrides "Carry the requested
work through delivery".

- On a production org, propose, show the plan and stop. Carry work through
  delivery only with an approval for each write, and in an approved sandbox or
  developer org each write needs an approval too.
- For each write: run the check-only step first (`sf project deploy validate`
  or `--dry-run`), capture or cite an independent before-state (or write down a
  manual recovery path), then run
  `torque approval request --workspace . --client NAME --change ID --org ALIAS -- <exact command>`.
  Tell the consultant the request ID and stop. Run the exact command it prints,
  from the same folder, only after the consultant says it is granted.
- One write per approval. Do not chain, pipe or redirect a write command.
- Never run `torque approval grant` or `deny`, `torque client consent`,
  `torque launch`, `torque workspace ai-access`, `sf alias set` or
  `sf config set`, and never edit consent, approval or Salesforce CLI files.
- Before running a script or any program the gate cannot check, tell the
  consultant whether it reaches an org and what it does; the host will ask them.
- Browser: ask for a browser window (`torque approval request --browser
  --purpose TEXT`) before any Setup or record change through the browser.
  Reading pages is fine.
