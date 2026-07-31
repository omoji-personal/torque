# Live verification

Never guess object, field, record-type, or flow API names. Verify against the target org
before referencing them in SOQL, metadata, or a deploy.

- Field existence: query `FieldDefinition` via the Tooling API rather than pulling a full
  describe into context (targeted, cheap). `describe_first` in the harness proves a known
  field resolves and a hallucinated one is refused.
- Namespaces: a managed field may carry a prefix in a subscriber org (`ns__Field__c`) and
  none in source. Resolve per org; if the local corpus and the live org disagree, the org
  wins.

ENFORCEMENT: harness-enforced (describe_first)
