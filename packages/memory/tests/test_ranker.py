"""Ranker tests — formula edge cases + adaptive K + eviction tie-break."""
import time
from dataclasses import dataclass

from jsc_memory import ranker


@dataclass
class _MockLesson:
    confidence: str = "MEDIUM"
    last_seen: int = 0
    captured_at: int = 0
    helpful_count: int = 0
    stale_count: int = 0
    id: str = "abc"


def test_surface_score_recent_high_confidence():
    now = 1000000
    l = _MockLesson(confidence="HIGH", last_seen=now)
    score = ranker.surface_score(l, now=now)
    assert score > 1.0


def test_surface_score_ancient_clamps_recency():
    now = 1000000
    # 5 years ago
    l = _MockLesson(confidence="HIGH", last_seen=now - (365 * 5 * 86400))
    score = ranker.surface_score(l, now=now)
    # Should not be 0 (MIN_RECENCY > 0)
    assert score > 0
    assert score < 1.0  # but heavily decayed


def test_surface_score_future_timestamp_does_not_overflow():
    now = 1000000
    l = _MockLesson(confidence="HIGH", last_seen=now + (365 * 100 * 86400))  # 100yr future
    # Should NOT raise OverflowError
    score = ranker.surface_score(l, now=now)
    assert score > 0  # clamps days to >= 0


def test_unknown_confidence_safe_default():
    now = 1000000
    l = _MockLesson(confidence="WEIRD_LABEL", last_seen=now)
    # Should NOT raise KeyError
    score = ranker.surface_score(l, now=now)
    assert score > 0


def test_feedback_clamped_high():
    now = 1000000
    l = _MockLesson(confidence="MEDIUM", last_seen=now,
                    helpful_count=100, stale_count=0)
    score = ranker.surface_score(l, now=now)
    # feedback caps at 3.0
    expected_max = 1.0 * 1.5 * 3.0
    assert score <= expected_max + 0.01


def test_feedback_clamped_low():
    now = 1000000
    l = _MockLesson(confidence="MEDIUM", last_seen=now,
                    helpful_count=0, stale_count=100)
    score = ranker.surface_score(l, now=now)
    # feedback floors at 0.1
    expected_min = 1.0 * 1.5 * 0.1
    assert score >= expected_min - 0.01


def test_adaptive_k_empty_queue():
    assert ranker.adaptive_surface_count(0) == 3


def test_adaptive_k_small_queue():
    assert ranker.adaptive_surface_count(5) == 3
    assert ranker.adaptive_surface_count(20) == 3


def test_adaptive_k_medium_queue():
    assert ranker.adaptive_surface_count(50) == 5


def test_adaptive_k_full_queue():
    assert ranker.adaptive_surface_count(100) == 10


def test_adaptive_k_overflow_queue():
    assert ranker.adaptive_surface_count(1000) == 10  # capped at 10


def test_eviction_key_total_ordering():
    """Tie-break by (surface_score ASC, captured_at ASC, lesson_id ASC) — deterministic."""
    now = 1000000
    a = _MockLesson(confidence="LOW", last_seen=now - 86400 * 30, captured_at=100, id="a")
    b = _MockLesson(confidence="LOW", last_seen=now - 86400 * 30, captured_at=200, id="b")
    c = _MockLesson(confidence="LOW", last_seen=now - 86400 * 30, captured_at=100, id="c")

    keys = [(ranker.eviction_key(x), x.id) for x in (a, b, c)]
    keys.sort()
    # a (captured_at=100, id=a) before c (captured_at=100, id=c) before b (captured_at=200)
    assert [k[1] for k in keys] == ["a", "c", "b"]


def test_eviction_deterministic_across_load_orders():
    """Per Codex R4 reproduction: forward + reversed load must produce same eviction order."""
    now = 1000000
    lessons = [
        _MockLesson(confidence="LOW", last_seen=now, captured_at=i, id=f"l{i:03d}")
        for i in range(10)
    ]
    # All same surface_score (HIGH+recent same params)
    forward_sorted = sorted(lessons, key=ranker.eviction_key)
    reversed_sorted = sorted(lessons[::-1], key=ranker.eviction_key)
    assert [l.id for l in forward_sorted] == [l.id for l in reversed_sorted]
