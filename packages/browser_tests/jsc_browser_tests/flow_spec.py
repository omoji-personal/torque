"""flow_spec.py — declarative flow metadata + the runtime ctx object."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Re-export Variation from runner so there is ONE Variation type.
from .runner import Variation  # noqa: F401


@dataclass
class FlowSpec:
    """Declarative metadata attached to a flow as `MyFlow.spec`."""
    name: str
    workflow: str                       # CANONICAL qa-router E-row id (E7=Portal, E8=Reporting)
    objects: list[str] = field(default_factory=list)
    writes: bool = True  # custom flows must explicitly declare read-only behavior
    profiles: list[str] = field(default_factory=lambda: ["admin"])
    requires: list[str] = field(default_factory=list)
    variations: list[Variation] = field(default_factory=list)


@dataclass
class FlowCtx:
    """Everything a flow needs at runtime — the single injected environment."""
    target_org: str
    instance_url: str
    org_id_18: str
    runid: str                          # scopes all TEST- records + teardown
    run_dir: Path                       # artifact/screenshot dir (was the engine's run_dir positional)
    variation: Variation                # the variation this cell runs (was verify's positional)
    profile: str
    values: dict                        # client-values.yaml overrides (baseline defaults)
    seed: dict                          # test-users.json entry for `profile`
    handles: dict                       # ids from provision(), consumed by run/verify/teardown
    page: Any                           # playwright Page (None in offline tests)
    sf: Any                             # SfClient (FakeSfClient offline)
