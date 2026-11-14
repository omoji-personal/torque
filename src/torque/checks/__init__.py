"""`torque check`: static checks of Salesforce metadata before a deploy, a reader for saved deploy
results, and a read-only comparison of what a deploy left in the org.

Modules: `xmlread` (line-aware XML), `components` (files to components), `deploy_result` (saved
`sf project deploy` JSON), `scope` (which components are the change), `rules*` (the checks),
`analyzer` (Salesforce Code Analyzer merge), `report` (text and JSON), `deployed` (after a deploy),
`command` (the CLI). The engine uses the standard library only.
"""
