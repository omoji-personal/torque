"""Is a person at a real terminal, outside the agent session?

This checks the agent's tool surface, not an operating-system boundary: code the
agent writes and runs under the same account can fake a terminal. See
docs/connected-approval.md for the limits."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Presence:
    ok: bool
    reason: str
