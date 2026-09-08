"""Ranker — surface_score formula + decay logic + adaptive K.

Per plan-v5: NO auto-promotion (Claude reviews everything). Ranker only
controls surfacing ORDER (which lessons surface first in session-start
injection).

Formula (clamped + bounded per R3 CODEX-F6 fix):
    score = recency × confidence × feedback
where:
    days = clamp(now - last_seen, [0, 180])
    recency = exp(-days / 30) clamped to [min_recency, 1.0]
    confidence = .get(label, default=1.0) — safe for unknown labels
    feedback = clamp(1 + (helpful - stale) * 0.5, [0.1, 3.0])
"""

from __future__ import annotations

import math
import time
from typing import Optional


CONFIDENCE_WEIGHTS = {
    "HIGHEST": 5.0,
    "HIGH": 3.0,
    "MEDIUM": 1.5,
    "LOW": 1.0,
}

MAX_DECAY_DAYS = 180
MIN_RECENCY = 0.001  # avoid score=0 underflow making sorting unstable


def surface_score(lesson, now: Optional[int] = None) -> float:
    """Compute ranker score for a Lesson. Bounded + deterministic.

    `lesson` is jsc_memory.storage.Lesson (or any object with the right attrs).
    """
    if now is None:
        now = int(time.time())

    last_seen = getattr(lesson, "last_seen", 0) or getattr(lesson, "captured_at", 0)
    days_raw = (now - last_seen) / 86400.0
    days = max(0.0, min(days_raw, float(MAX_DECAY_DAYS)))
    recency = max(MIN_RECENCY, min(1.0, math.exp(-days / 30.0)))

    confidence_label = getattr(lesson, "confidence", "LOW")
    confidence = CONFIDENCE_WEIGHTS.get(confidence_label, 1.0)

    helpful = getattr(lesson, "helpful_count", 0)
    stale = getattr(lesson, "stale_count", 0)
    feedback = max(0.1, min(3.0, 1.0 + (helpful - stale) * 0.5))

    return recency * confidence * feedback


def adaptive_surface_count(queue_size: int) -> int:
    """How many lessons to surface at session start, given current queue size.

    Per plan-v5 R4 CODEX-F1 fix: K = min(10, max(3, queue_size // 10)).
    """
    return min(10, max(3, queue_size // 10))


def adjacent_signature(s1: str, s2: str) -> bool:
    """Lightweight check if two signature strings represent the same root cause."""
    if not s1 or not s2:
        return False
    if s1 == s2:
        return True
    # Tokenize on | (signature delimiter)
    t1 = set(s1.split("|"))
    t2 = set(s2.split("|"))
    overlap = len(t1 & t2)
    return overlap >= max(2, min(len(t1), len(t2)) - 1)


def eviction_key(lesson) -> tuple:
    """Total ordering for cap-full eviction.

    Per plan-v5 R4 F2 fix: (surface_score ASC, captured_at ASC, lesson_id ASC).
    Drop the LOWEST score first; ties broken by oldest first; final tie by id
    for full determinism regardless of filesystem load order.
    """
    return (
        surface_score(lesson),
        getattr(lesson, "captured_at", 0),
        getattr(lesson, "id", ""),
    )
