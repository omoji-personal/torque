"""Session-start injector — surfaces top-K relevant lessons to Claude.

Per plan-v5: index-backed (read index_top.json), bounded by 500ms budget,
adaptive K = min(10, max(3, queue_size // 10)).
"""

from __future__ import annotations
from jsc_common.workspace import workspace_root

import os
import pathlib
import time
from typing import Optional

from . import index
from . import ranker
from . import storage

LESSON_INJECTION_BUDGET_MS = int(os.environ.get("JSC_LESSON_INJECT_BUDGET_MS", "500"))


def _signal_relevance(lesson_dict: dict, signals: dict) -> float:
    """Score relevance based on overlap with current session signals."""
    boost = 1.0

    # Client scope match
    cur_clients = signals.get("clients", []) or []
    lesson_clients = lesson_dict.get("client_scope", []) or []
    if cur_clients and lesson_clients:
        if any(c in lesson_clients for c in cur_clients):
            boost *= 2.0

    # Keyword overlap
    cur_keywords = set(signals.get("keywords", []) or [])
    lesson_keywords = set(lesson_dict.get("signature_keywords", []) or [])
    if cur_keywords and lesson_keywords:
        overlap = len(cur_keywords & lesson_keywords)
        if overlap > 0:
            boost *= (1.0 + 0.3 * min(overlap, 5))

    return boost


def top_k_for_session(
    signals: dict,
    max_count: int = 3,
    deadline: Optional[float] = None,
) -> list[dict]:
    """Read index_top.json, filter+rank by relevance, return top max_count.

    Returns list of dicts (NOT Lesson objects) with the fields needed for
    injection rendering: id, title, short, state, confidence, captured_at,
    last_seen, helpful_count, stale_count, missed_review_count, path.

    Fail-open: empty list on any error or budget exceeded.
    """
    if deadline is None:
        deadline = time.monotonic() + (LESSON_INJECTION_BUDGET_MS / 1000.0)

    if time.monotonic() > deadline:
        return []

    data = index.read_index_top(deadline_ts=deadline)
    if not data:
        return []

    if time.monotonic() > deadline:
        return []

    candidates = data.get("lessons", [])
    if not candidates:
        return []

    # Apply relevance boost
    scored = []
    for c in candidates:
        if time.monotonic() > deadline:
            break
        try:
            base_score = c.get("score", 0.0)
            boost = _signal_relevance(c, signals)
            adjusted = base_score * boost
            scored.append((adjusted, c))
        except Exception:
            continue

    if not scored:
        return []

    scored.sort(key=lambda x: x[0], reverse=True)

    # Add path field for rendering
    out = []
    for _, c in scored[:max_count]:
        if time.monotonic() > deadline:
            break
        # Convert to render-friendly dict
        out.append({
            **c,
            "path": _lesson_path(c),
        })
    return out


def _lesson_path(lesson_dict: dict) -> str:
    """Return readable path hint for the lesson source file."""
    state = lesson_dict.get("state", "review_pending")
    captured = lesson_dict.get("captured_at", 0)
    lid = lesson_dict.get("id", "")
    if state == "review_pending":
        return f"{storage.review_queue_dir() / f'{captured}-{lid[:12]}.json'}"
    if state == "active":
        return str(storage.active_lessons_path())
    if state == "archive":
        return f"{storage.archive_dir() / f'{captured}-{lid[:12]}.json'}"
    return ""


def extract_session_signals(repo_root: Optional[pathlib.Path] = None) -> dict:
    """Extract relevance signals from current session context.

    Returns dict with: cwd, git_branch, clients, keywords.
    Best-effort; fail-open on any error.
    """
    import subprocess

    if repo_root is None:
        repo_root = workspace_root()

    out = {
        "cwd": str(repo_root),
        "git_branch": "",
        "clients": [],
        "keywords": [],
    }

    # Git branch
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "branch", "--show-current"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            out["git_branch"] = result.stdout.strip()
    except Exception:
        pass

    # The launcher already selected one client. Never scan sibling clients.
    out["clients"] = [repo_root.name]

    # Keywords: from current branch name + cwd path
    try:
        import re
        words = re.findall(r"[a-zA-Z][\w-]+", out["git_branch"] + " " + str(repo_root))
        out["keywords"] = list({w.lower() for w in words if len(w) > 2})[:20]
    except Exception:
        pass

    return out


def render_injection_section(lessons: list[dict]) -> str:
    """Render the top-K lessons for inclusion in session-start context.

    Returns the markdown block to print, or "" if no lessons.

    Per plan-v5: includes mandatory Claude-as-gate prose instructing Claude
    to evaluate + surface in first turn (operator-visible).
    """
    if not lessons:
        return ""

    review_pending = [l for l in lessons if l.get("state") == "review_pending"]
    active = [l for l in lessons if l.get("state") == "active"]

    out = []
    if review_pending:
        out.append("## Lessons in REVIEW QUEUE — Claude must evaluate in first turn")
        out.append("")
        out.append("**Mandatory: in your first response to the operator, explicitly evaluate")
        out.append("each lesson below. For each, decide:**")
        out.append("- Is this lesson still accurate?")
        out.append("- Is it relevant to the current task?")
        out.append("- Then call `/lesson-helpful <id>` (relevant+accurate) or `/lesson-stale <id>` (no/outdated).")
        out.append("- Surface your evaluation to the operator so they can override if you got it wrong.")
        out.append("")
        for l in review_pending:
            out.append(f"### REVIEW: {l.get('title', '')}  `id={l.get('id', '')[:12]}`")
            out.append(f"{l.get('short', '')}")
            out.append(f"- Captured: {_human_ts(l.get('captured_at', 0))} from `{l.get('confidence', '')}` confidence")
            out.append(f"- Full text: `{l.get('path', '')}`")
            out.append("")

    if active:
        out.append("## Active lessons relevant to this session")
        out.append("")
        for l in active:
            out.append(f"### {l.get('title', '')}  `id={l.get('id', '')[:12]}`")
            out.append(f"{l.get('short', '')}")
            out.append(f"- Helpful: {l.get('helpful_count', 0)}× | Stale: {l.get('stale_count', 0)}× | Last seen: {_human_ts(l.get('last_seen', 0))}")
            out.append(f"- Mark stale if outdated: `/lesson-stale {l.get('id', '')[:12]}`")
            out.append("")

    return "\n".join(out) + "\n" if out else ""


def _human_ts(ts: int) -> str:
    """Format unix ts → 'YYYY-MM-DD HH:MM'."""
    if not ts:
        return "unknown"
    try:
        from datetime import datetime
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "unknown"
