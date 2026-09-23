"""jsc_memory CLI — single entry point for /lesson-* slash commands.

Usage:
    python3 -m jsc_memory.cli <subcommand> [args]

Subcommands:
    capture [content]    — explicit operator capture (writes to L1 review queue)
    helpful <id>         — mark lesson helpful (review_pending → active OR boost)
    stale <id>           — mark lesson stale (≥2 → archive)
    show [pending|active|archive] — list lessons in given state
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time

from . import index
from . import storage


def _cmd_capture(args: argparse.Namespace) -> int:
    """Explicit lesson capture from operator."""
    content = args.content or sys.stdin.read().strip()
    if not content:
        print("ERROR: no content provided", file=sys.stderr)
        return 2

    captured_at = int(time.time())
    lesson_id = hashlib.sha256(f"{content}{captured_at}".encode()).hexdigest()[:16]
    title_words = content.split()[:8]
    title = "[explicit] " + " ".join(title_words)
    short = content.split("\n")[0][:200]

    lesson = storage.Lesson(
        id=lesson_id,
        title=title[:120],
        short=short,
        full_text=content,
        confidence="HIGHEST",
        trigger="explicit_lesson",
        captured_at=captured_at,
        last_seen=captured_at,
        state="review_pending",
    )
    p = storage.write_review_candidate(lesson)
    index.rebuild_index()
    print(f"Captured lesson {lesson.id[:12]} → {p}")
    return 0


def _cmd_helpful(args: argparse.Namespace) -> int:
    """Promote review_pending → active OR boost active score."""
    result = storage.promote_to_active(args.lesson_id)
    if result is None:
        print(f"ERROR: lesson {args.lesson_id} not found", file=sys.stderr)
        return 1
    index.rebuild_index()
    print(f"Marked helpful: {result.id[:12]} ({result.state}); helpful_count={result.helpful_count}")
    return 0


def _cmd_stale(args: argparse.Namespace) -> int:
    """Increment stale_count; archive at ≥2."""
    result = storage.mark_stale(args.lesson_id)
    if result is None:
        print(f"ERROR: lesson {args.lesson_id} not found", file=sys.stderr)
        return 1
    index.rebuild_index()
    print(f"Marked stale: {result.id[:12]} (state={result.state}, stale_count={result.stale_count})")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    """List lessons in given state. Pending sorted by missed_review_count DESC."""
    state = args.state or "pending"
    if state == "pending":
        lessons = storage.list_review_pending()
        lessons.sort(key=lambda l: (-l.missed_review_count, -l.captured_at))
    elif state == "active":
        lessons = storage.list_active()
        lessons.sort(key=lambda l: -l.last_seen)
    elif state == "archive":
        lessons = []
        for p in sorted(storage.archive_dir().glob("*.json")):
            try:
                lessons.append(storage.Lesson.from_dict(json.loads(p.read_text())))
            except Exception:
                continue
        lessons.sort(key=lambda l: -l.captured_at)
    else:
        print(f"ERROR: unknown state {state}", file=sys.stderr)
        return 2

    if not lessons:
        print(f"(no lessons in {state})")
        return 0

    print(f"=== {state.upper()} ({len(lessons)}) ===")
    for l in lessons:
        marker = ""
        if state == "pending" and l.missed_review_count > 0:
            marker = f" [skipped {l.missed_review_count}×]"
        print(f"  {l.id[:12]} | {l.confidence:8s} | {l.trigger:30s} | {l.title[:60]}{marker}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Torque lesson management CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_capture = sub.add_parser("capture", help="Explicit lesson capture")
    p_capture.add_argument("content", nargs="?", default="", help="Lesson content (or read from stdin)")
    p_capture.set_defaults(func=_cmd_capture)

    p_helpful = sub.add_parser("helpful", help="Mark lesson helpful")
    p_helpful.add_argument("lesson_id", help="Lesson id (or 12-char prefix)")
    p_helpful.set_defaults(func=_cmd_helpful)

    p_stale = sub.add_parser("stale", help="Mark lesson stale")
    p_stale.add_argument("lesson_id", help="Lesson id (or 12-char prefix)")
    p_stale.set_defaults(func=_cmd_stale)

    p_show = sub.add_parser("show", help="List lessons by state")
    p_show.add_argument("state", nargs="?", default="pending", choices=["pending", "active", "archive"])
    p_show.set_defaults(func=_cmd_show)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
