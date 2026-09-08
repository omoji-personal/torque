"""jsc_memory — recursive lesson capture system for JSC.

See `.claude/rules/lesson-capture.md` for the operator-facing contract.
See `audits/2026-05-12-lesson-capture-redesign/plan-v5.md` for the design.

Public API:
- `storage`: load/save L1 (review queue) / L2 (active) / L3 (archive)
- `spool`: per-session event spool (capture-time)
- `scrubber`: source-typed PII scrubber (SOQL/JSON/CLI-table/narrative)
- `capture`: Stop hook synthesis (spool → candidates)
- `ranker`: surface_score formula + decay
- `injector`: index-backed top-K relevance match for SessionStart
- `index`: atomic + locked index updates
- `cli`: CLI entry point (capture, helpful, stale, show)

Compatibility shim:
- `storage.load_framework_memory()` preserves the symbol referenced in
  `packages/hooks/session_start_context.py:_proactive_l3_section`. It returns
  an object whose `.lessons` attribute is empty (no L3 framework lessons in v1).
"""

__version__ = "1.0.0"
__schema_version__ = 1

from . import storage  # noqa: F401
from . import scrubber  # noqa: F401
from . import spool  # noqa: F401
from . import index  # noqa: F401
from . import ranker  # noqa: F401
from . import capture  # noqa: F401
from . import injector  # noqa: F401
