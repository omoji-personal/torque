# /flow-analyzer

Explain, review or fix a flow from its active definition and its actual behavior. Use it when someone asks what a flow does, why it failed, or how to improve it.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Read skill `salesforce-flow` (`salesforce-flow/references/review.md`) first.

{{lessons --task flow-build --type Flow}}

1. Locate the flow in the selected org or the supplied metadata. Retrieve its current definition
   (`/retrieve-current`) and name the active version apart from the latest or draft one:
   `torque advisory flow --target-org <org-alias> --api-name <FlowApiName> --json`.
2. Run `torque check metadata <flow-file> [--baseline <folder-retrieved-before-the-change>]`
   first. It reports missing fault paths, data elements inside a loop, missing entry conditions,
   flows that rerun on every update, hard-coded Ids, choice fields compared to text in formulas,
   screen actions without a trigger and undescribed elements, and merges Code Analyzer's flow
   rules when installed; with a baseline it labels what the flow already had as inherited. Then
   read the logic for what no check sees.
3. Explain purpose, entry conditions, trigger timing, screen path, decisions, variables,
   subflows, formulas, data changes and external actions, tying each statement to an element.
4. Review bulk behavior, recursion, transaction boundaries, nulls, permissions and running
   context, naming and maintainability. Offer a simpler field, formula or before-save design only
   when it does the same job: before-save fits a flow that only sets fields on the record being
   saved with nothing depending on the saved values; after save fits work that needs the record's
   Id or changes other records. Ask for the reason to be in the flow's description.
5. Separate what the source shows from what was observed at runtime. For a fix, keep the
   retrieved baseline, edit only the elements the fix needs (never regenerate the whole flow),
   validate (`/validate-change`), test the relevant paths, deploy within the authorized scope and
   confirm which version is active afterwards.

Save the explanation, the rationale for a change, the tests and the paths not tested. For
discovery or training write plain-language steps; for an implementation give the exact elements
and the diff.
