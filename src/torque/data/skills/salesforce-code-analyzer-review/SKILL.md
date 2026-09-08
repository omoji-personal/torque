---
name: salesforce-code-analyzer-review
description: Scope and interpret Salesforce Code Analyzer review for Apex, Lightning, Flow, and metadata changes. Use to choose review targets, assess static-analysis coverage, or interpret analyzer findings.
---

# Salesforce Code Analyzer review

Identify the local files or diff and the business behavior being changed.
Preserve unrelated edits. Use the installed Salesforce Code Analyzer interface
or configured connector; inspect its help/configuration before assuming a version.
Choose the relevant engines and a bounded target so generated or vendor files
do not bury useful findings.

Distinguish code defects, analyzer configuration failures, and surfaces the tool
does not cover. A clean scan does not establish that a deployment, access model,
or user workflow works. Report findings with file locations and practical impact,
then name any necessary runtime validation. If the request includes fixes,
continue through remediation and rerun only the affected checks.

Review alone does not imply an org deployment. Use the user's existing scope
and authorization for any execution that follows.
