# /probes

Generate editable Apex test drafts and guide their validation and execution.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

The native command generates Apex test source from supplied class files; it does
not run live platform probes, deploy classes or execute tests by itself.

```sh
torque probes --target-class <class-path.cls> --output <private-output-directory>
```

Use `--target-dir <source-directory>` instead to generate from a directory. Always
choose an explicit private output directory so generated files do not unexpectedly
appear alongside the input source. Inspect `torque probes --help` for the installed
generator contract. Actual `@isTest` or legacy `testMethod` classes are skipped;
production names containing `Test` remain eligible. Existing generated class and
metadata files are preserved. Choose a new output directory to regenerate, or use
`--force` when you explicitly intend to replace both. API version follows the
source companion metadata, with 61.0 as fallback; `--api-version` overrides it.

Each discovered supported public/global method has typed null inputs and, where
applicable, an empty-collection case. Nonvoid returns are stored as `actual`.
Every case intentionally fails at its `DRAFT` assertion until you replace that
assertion with an explicit expected value, state or exception. Unexpected target
exceptions also fail; they are not logged as success. Unsupported signatures or
receivers remain visible in comments and a review case rather than guessed calls.

Review client prerequisites, target side effects, permissions and applicable API
version. The generator does not invent Account/User data, bulk work, governor
budgets or FLS coverage. Drafts may require adaptation before they compile or
test the intended business behavior. QA reports generation as `MANUAL_REQUIRED`
with compiled/executed false, even when all draft files were generated. When execution
is requested, use normal Salesforce validation/test tools in the selected authorized
org; that is a guided continuation, not a built-in probe-runner capability.

Capture actual test results and cleanup when execution occurs. “Files generated”
is different from “compiled,” “tests passed,” and “business behavior verified.”
For AI-output fixtures, the separate `torque ai-regression --help` route describes
the inherited replay/validation adapter and its provider prerequisites.
