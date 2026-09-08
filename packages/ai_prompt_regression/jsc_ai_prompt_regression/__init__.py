"""jsc_ai_prompt_regression — offline prompt-replay + JSON-shape regression harness.

Closes Category D (AI features) coverage in the QA matrix per qa-orchestration.md.
Replays a frozen prompt template against an LLM transport (gemini / claude),
parses the JSON output, and validates the shape against a JsonExtractor +
Example_Extraction_Target__mdt CMDT contract.
"""

from .contracts import (
    TargetSpec,
    ValidationFinding,
    ValidationResult,
    parse_target_spec_yaml,
    validate_against_spec,
)
from .harness import (
    Fixture,
    ReplayResult,
    load_fixture,
    replay_fixture,
)

__all__ = [
    "TargetSpec",
    "ValidationFinding",
    "ValidationResult",
    "parse_target_spec_yaml",
    "validate_against_spec",
    "Fixture",
    "ReplayResult",
    "load_fixture",
    "replay_fixture",
]
