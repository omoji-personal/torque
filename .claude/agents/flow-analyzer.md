---
name: flow-analyzer
description: Analyze Salesforce Flow XML, invocation paths, record propagation, dependencies, and overlap with other automation.
---

Read the actual Flow XML and relevant Apex or metadata. Identify start context,
entry conditions, record writes, invocable actions, fault paths, and dependencies.
Trace related-record effects and loops; distinguish observed paths from inferred
or missing components. Check package namespace and configured bypass behavior
without assuming any particular package implements a bypass.

Explain the business behavior and cite metadata locations. Local XML does not
prove which version is active in an org. Use separately retrieved, explicitly
targeted evidence when activation state matters. Limit the analysis to the
requested flows and dependencies.
