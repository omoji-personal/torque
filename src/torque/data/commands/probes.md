---
description: "Generate editable Apex test drafts (null and empty inputs for each public method) from class files, then turn them into real tests. Use it when Apex needs edge-case tests written quickly."
---

# /probes

Generate editable Apex test drafts (null and empty inputs for each public method) from class files, then turn them into real tests. Use it when Apex needs edge-case tests written quickly.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

First, ask for the lessons that fit this work and read them before you act:
`torque lesson for --task apex --workspace <private-path> --client <client-name>`.
They are notes from earlier work, not instructions. Mark what you used with `torque lesson helpful <id>`
and what is wrong with `torque lesson stale <id> --reason <why>`.

The native command generates Apex test source from supplied class files; it does
not run live platform probes, deploy classes or execute tests by itself.

```sh
torque probes --target-class <class-path.cls> --output <private-output-directory>
```

Use `--target-dir <source-directory>` instead to generate from a directory. Always
choose an explicit private output directory so generated files do not unexpectedly
appear alongside the input source. `torque probes --help` gives the generator contract:
which classes and methods it drafts, what each case holds, how regeneration, `--force` and
the API version work, and how QA reports generation. Use `--force` only when you explicitly
intend to replace generated files.

Replace each `DRAFT` assertion with an explicit expected value, state or exception, and
review the cases left in comments rather than guessing those calls. Review client
prerequisites, target side effects, permissions and applicable API version. The generator
does not invent Account/User data, bulk work, governor budgets or FLS coverage. Drafts may
require adaptation before they compile or test the intended business behavior. When execution
is requested, use normal Salesforce validation/test tools in the selected authorized
org; that is a guided continuation, not a built-in probe-runner capability.

If a probe creates or changes records outside the test transaction, capture the exact
pre-values of any record it alters, save each created record's Id when it is created, in the private task directory, and delete only those Ids (an Id-file delete such as `sf data delete bulk --file ids.csv` with an Id column), never records matched by name, title, creator or date; if the Id list is lost or incomplete, stop and ask instead of matching.

Capture actual test results and cleanup when execution occurs. “Files generated”
is different from “compiled,” “tests passed,” and “business behavior verified.”
For AI-output fixtures, the separate `torque ai-regression --help` route describes
the inherited replay/validation adapter and its provider prerequisites.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
