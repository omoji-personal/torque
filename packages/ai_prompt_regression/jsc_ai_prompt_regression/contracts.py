"""contracts.py — JSON-shape validators mirroring JsonExtractor + CMDT.

Maps directly to `Example_Extraction_Target__mdt` (see
`AI and Package Resources/ai-offering/extractor/CMDT-spec.md`):

- `Mode__c=Fields`: extracts dot-notation paths from JSON. Validator asserts
  every required path resolves to a non-null scalar (per Field_Paths__c).
- `Mode__c=Records`: extracts a list at a given Base_Path. Validator asserts
  the path resolves to a list AND every entry has every mapped JSON key
  (per Field_Mappings__c).

Validation is OFFLINE — no Salesforce API call. The harness consumes this
to assert prompt outputs satisfy the CMDT-declared schema BEFORE deploy.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


_DOTPATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_TRUE_STRINGS = frozenset({"true", "t", "1", "yes", "y", "on"})
_FALSE_STRINGS = frozenset({"false", "f", "0", "no", "n", "off"})


def _coerce_bool(value, *, default: bool, field_name: str) -> bool:
    """Coerce JSON/YAML/CMDT-style boolean values, including string forms.

    Closes codex-R1-P2-05: bool('false') is True; this routes string forms
    through an explicit allow-list instead.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _TRUE_STRINGS:
            return True
        if v in _FALSE_STRINGS:
            return False
        if v == "":
            return default
        raise ValueError(f"{field_name}: ambiguous boolean string {value!r}")
    raise ValueError(f"{field_name}: cannot coerce {type(value).__name__} to bool")


@dataclass
class TargetSpec:
    """One row of Example_Extraction_Target__mdt, in-memory."""
    target_name: str
    configuration_set: str
    mode: str  # 'Fields' | 'Records'
    base_path: str = ""
    sobject_type: str = ""
    field_paths: list[str] = field(default_factory=list)  # for Fields mode
    field_mappings: dict[str, str] = field(default_factory=dict)  # jsonKey → SF field, for Records mode
    parent_field: str = ""
    enforce_fls: bool = True
    single_object: bool = False
    description: str = ""
    active: bool = True
    strict_keys: bool = False  # When True, validator flags any payload key not declared in the spec (R1 CONSENSUS-5)
    allow_null_required: bool = False  # When True, null required values are P2 not P1 (R1 codex-P1-05)


@dataclass
class ValidationFinding:
    severity: str  # 'P0' | 'P1' | 'P2' | 'INFO'
    target_name: str
    path: str
    message: str


@dataclass
class ValidationResult:
    status: str  # 'PASS' | 'FAIL' | 'PARSE_FAIL'
    findings: list[ValidationFinding] = field(default_factory=list)
    parsed_payload: Any = None  # the JSON dict if parsing succeeded

    def is_ok(self) -> bool:
        return self.status == "PASS" and not any(
            f.severity in ("P0", "P1") for f in self.findings
        )

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "findings": [
                {"severity": f.severity, "target_name": f.target_name,
                 "path": f.path, "message": f.message}
                for f in self.findings
            ],
        }


def _resolve_dotpath(payload: Any, dotpath: str) -> tuple[bool, Any]:
    """Walk a dot-notation path. Returns (found, value)."""
    if not dotpath:
        return True, payload
    cur = payload
    for segment in dotpath.split("."):
        if not isinstance(cur, dict):
            return False, None
        if segment not in cur:
            return False, None
        cur = cur[segment]
    return True, cur


def parse_target_spec_yaml(raw: dict) -> TargetSpec:
    """Build a TargetSpec from a YAML/JSON-like dict (CMDT row mirror).

    Accepts either CMDT field API names (e.g., `Target_Name__c`) or short
    snake_case keys (e.g., `target_name`) — operators write specs in YAML
    using the friendlier shape.
    """
    def get(*keys):
        for k in keys:
            if k in raw:
                return raw[k]
        return None

    name = get("target_name", "Target_Name__c")
    if not name:
        raise ValueError("target_name (or Target_Name__c) is required")
    config_set = get("configuration_set", "Configuration_Set__c") or ""
    mode = (get("mode", "Mode__c") or "").strip()
    if mode not in ("Fields", "Records"):
        raise ValueError(f"mode must be 'Fields' or 'Records' (got {mode!r})")

    spec = TargetSpec(
        target_name=str(name),
        configuration_set=str(config_set),
        mode=mode,
        base_path=str(get("base_path", "Base_Path__c") or ""),
        sobject_type=str(get("sobject_type", "SObject_Type__c") or ""),
        parent_field=str(get("parent_field", "Parent_Field__c") or ""),
        enforce_fls=_coerce_bool(get("enforce_fls", "Enforce_FLS__c"), default=True, field_name="enforce_fls"),
        single_object=_coerce_bool(get("single_object", "Single_Object__c"), default=False, field_name="single_object"),
        description=str(get("description", "Description__c") or ""),
        active=_coerce_bool(get("active", "Active__c"), default=True, field_name="active"),
        strict_keys=_coerce_bool(get("strict_keys", "Strict_Keys__c"), default=False, field_name="strict_keys"),
        allow_null_required=_coerce_bool(get("allow_null_required", "Allow_Null_Required__c"), default=False, field_name="allow_null_required"),
    )

    if mode == "Fields":
        raw_paths = get("field_paths", "Field_Paths__c") or []
        if isinstance(raw_paths, str):
            raw_paths = [p.strip() for p in re.split(r"[,\n]", raw_paths) if p.strip()]
        for p in raw_paths:
            if not _DOTPATH_RE.match(str(p)):
                raise ValueError(f"invalid dotpath in field_paths: {p!r}")
        spec.field_paths = [str(p) for p in raw_paths]
        if not spec.field_paths:
            raise ValueError("Fields mode requires field_paths")
    else:  # Records
        raw_map = get("field_mappings", "Field_Mappings__c") or {}
        if isinstance(raw_map, str):
            parsed = {}
            for line in raw_map.splitlines():
                line = line.strip()
                if not line:
                    continue
                if "=>" in line:
                    j, sf = line.split("=>", 1)
                elif "JSON Field:" in line and "Salesforce Field:" in line:
                    # "JSON Field: foo | Salesforce Field: bar__c"
                    parts = line.split("|")
                    if len(parts) != 2:
                        raise ValueError(f"unparseable field_mapping line: {line!r}")
                    j = parts[0].split(":", 1)[1]
                    sf = parts[1].split(":", 1)[1]
                else:
                    raise ValueError(f"unparseable field_mapping line: {line!r}")
                parsed[j.strip()] = sf.strip()
            raw_map = parsed
        if not isinstance(raw_map, dict) or not raw_map:
            raise ValueError("Records mode requires field_mappings (dict or text)")
        spec.field_mappings = {str(k): str(v) for k, v in raw_map.items()}
        if not spec.sobject_type:
            raise ValueError("Records mode requires sobject_type")

    return spec


def validate_against_spec(
    payload: Any,
    specs: list[TargetSpec],
) -> ValidationResult:
    """Validate a parsed JSON payload against one or more TargetSpec rows.

    Each spec runs independently; findings are aggregated. PASS = zero
    P0/P1 findings across all specs.
    """
    findings: list[ValidationFinding] = []

    if not isinstance(payload, dict):
        findings.append(ValidationFinding(
            "P0", "<root>", "",
            f"top-level payload must be a dict (got {type(payload).__name__})",
        ))
        return ValidationResult(status="FAIL", findings=findings, parsed_payload=payload)

    for spec in specs:
        if not spec.active:
            continue
        # Resolve base_path
        found, scoped = _resolve_dotpath(payload, spec.base_path)
        if not found:
            findings.append(ValidationFinding(
                "P0", spec.target_name, spec.base_path,
                f"base_path {spec.base_path!r} not found in payload",
            ))
            continue

        if spec.mode == "Fields":
            if not isinstance(scoped, dict):
                findings.append(ValidationFinding(
                    "P0", spec.target_name, spec.base_path,
                    f"Fields-mode base_path resolved to {type(scoped).__name__}, expected dict",
                ))
                continue
            # R1 CONSENSUS-5: strict_keys flags unknown extras as P1
            if spec.strict_keys:
                expected_top_keys = {fp.split(".", 1)[0] for fp in spec.field_paths}
                for k in scoped.keys():
                    if k not in expected_top_keys:
                        findings.append(ValidationFinding(
                            "P1", spec.target_name, k,
                            f"strict_keys: unknown key {k!r} in base_path {spec.base_path!r}",
                        ))
            for fp in spec.field_paths:
                ok, val = _resolve_dotpath(scoped, fp)
                if not ok:
                    findings.append(ValidationFinding(
                        "P1", spec.target_name, fp,
                        f"required field_path {fp!r} missing from base_path {spec.base_path!r}",
                    ))
                    continue
                # R1 codex-P1-05: null required values default to P1; allow_null_required override drops to P2
                if val is None:
                    sev = "P2" if spec.allow_null_required else "P1"
                    findings.append(ValidationFinding(
                        sev, spec.target_name, fp,
                        f"field_path {fp!r} present but null",
                    ))
                    continue
                # R1 codex-P1-05: required Fields-mode values must be scalar (not dict/list)
                if isinstance(val, (dict, list)):
                    findings.append(ValidationFinding(
                        "P1", spec.target_name, fp,
                        f"field_path {fp!r} is {type(val).__name__}, expected scalar",
                    ))
        else:  # Records
            if spec.single_object and isinstance(scoped, dict):
                items = [scoped]
            elif not isinstance(scoped, list):
                findings.append(ValidationFinding(
                    "P0", spec.target_name, spec.base_path,
                    f"Records-mode base_path resolved to {type(scoped).__name__}, expected list",
                ))
                continue
            else:
                items = scoped
            for idx, item in enumerate(items):
                if not isinstance(item, dict):
                    findings.append(ValidationFinding(
                        "P1", spec.target_name, f"{spec.base_path}[{idx}]",
                        f"records-mode item {idx} is {type(item).__name__}, expected dict",
                    ))
                    continue
                for json_key in spec.field_mappings.keys():
                    ok, val = _resolve_dotpath(item, json_key)
                    if not ok:
                        findings.append(ValidationFinding(
                            "P1", spec.target_name, f"{spec.base_path}[{idx}].{json_key}",
                            f"required mapped key {json_key!r} missing from records-mode item",
                        ))

    has_blocking = any(f.severity in ("P0", "P1") for f in findings)
    return ValidationResult(
        status="FAIL" if has_blocking else "PASS",
        findings=findings,
        parsed_payload=payload,
    )


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _strip_fences(raw: str) -> str:
    """Strip ```json ... ``` code fences, returning concatenated inner content
    if any fence is present, else the original string."""
    matches = _FENCE_RE.findall(raw)
    if matches:
        return "\n".join(m for m in matches if m.strip())
    return raw


def _find_first_balanced_json(text: str) -> tuple[int, int] | None:
    """Find the FIRST balanced `{...}` block in `text` using a brace-depth
    state machine that respects string literals (with backslash escapes).
    Returns (start, end_inclusive) or None if no balanced block exists.

    Closes R1 CONSENSUS-5 (a): greedy `find("{")` + `rfind("}")` corrupts
    JSON when the model emits prose containing `{...}` after the payload.
    """
    n = len(text)
    i = 0
    while i < n:
        if text[i] == "{":
            depth = 0
            in_string = False
            escape = False
            for j in range(i, n):
                c = text[j]
                if escape:
                    escape = False
                    continue
                if c == "\\" and in_string:
                    escape = True
                    continue
                if c == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        return i, j
            # Unbalanced from this `{`; advance to find the next opener
            i = i + 1
            continue
        i += 1
    return None


def safe_parse_json(raw: str) -> tuple[Any, str]:
    """Best-effort JSON parse with banner-prefix + code-fence tolerance.

    Strategy:
      1. Strip ```json ... ``` code fences if present.
      2. Find the FIRST balanced `{...}` block via brace-depth state machine
         that respects string literals.
      3. json.loads it.

    Returns (parsed_or_None, error_detail). On success error_detail is empty.
    """
    if not raw or not raw.strip():
        return None, "empty response"
    candidate_text = _strip_fences(raw)
    bounds = _find_first_balanced_json(candidate_text)
    if bounds is None:
        return None, "no balanced JSON object found in response"
    start, end = bounds
    candidate = candidate_text[start : end + 1]
    try:
        return json.loads(candidate), ""
    except json.JSONDecodeError as e:
        return None, f"json decode error: {e}"
