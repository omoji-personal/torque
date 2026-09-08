"""jsc-loganalyzer — closed-loop debug-log analyzer.

Adopted 2026-05-04 from claudeblazer (Apache-2.0). TAA Phase 5 P2-3.
"""

from jsc_loganalyzer.score import Finding, Result, score_findings

__all__ = ["Finding", "Result", "score_findings"]
__version__ = "0.1.0"
