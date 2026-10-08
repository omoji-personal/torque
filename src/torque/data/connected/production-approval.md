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
- Never run a command that prints or takes a credential, or that lists every
  org this machine is logged in to: `sf org display`, `sf org open --url-only`
  (or `-r`, `--json`), `sf org generate password`, `sf org login`,
  `sf org list`, `sf alias list`. The gate refuses them, with or without an
  approval. To check which org an alias points to, run
  `sf data query --target-org ALIAS -q "SELECT Id, Name, IsSandbox FROM Organization"`.
- Before running a script or any program the gate cannot check, tell the
  consultant whether it reaches an org and what it does; the host will ask them.
- Browser: use only Torque's own browser, `torque browser ... --target-org ALIAS`
  (or `torque qa` with an org). Ask for a browser window first
  (`torque approval request --browser --purpose TEXT --org ALIAS`) and stop
  until the consultant grants it. The gate refuses browser, devtools and
  desktop tools (Claude in Chrome, Playwright, computer use) in this mode, for
  reading a page as well as for changing one.
