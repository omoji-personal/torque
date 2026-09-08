"""
Health-score model + Finding/Result dataclasses.

Adopted 2026-05-04 from claudeblazer (Apache-2.0). TAA Phase 5 P2-3.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


SEVERITY_DEDUCTIONS = {1: 15, 2: 5, 3: 1}  # P0 → -15, P1 → -5, P2 → -1


@dataclass
class Finding:
    category: str
    severity: int  # 1 / 2 / 3
    message: str
    line: int = 0  # line in the debug log
    context: str = ""  # short surrounding snippet

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Result:
    score: int
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "findings": [f.to_dict() for f in self.findings],
            "summary": {
                "p0_count": sum(1 for f in self.findings if f.severity == 1),
                "p1_count": sum(1 for f in self.findings if f.severity == 2),
                "p2_count": sum(1 for f in self.findings if f.severity == 3),
                "total": len(self.findings),
            },
        }


def score_findings(findings: list[Finding]) -> int:
    """Compute the 0–100 health score from a finding list."""
    score = 100
    for f in findings:
        score -= SEVERITY_DEDUCTIONS.get(f.severity, 0)
    return max(0, min(100, score))
