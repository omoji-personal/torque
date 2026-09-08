"""CLI for jsc_ai_prompt_regression."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .harness import (
    DEFAULT_MODEL_CHAIN,
    DEFAULT_TIMEOUT_S,
    gemini_available,
    load_fixture,
    replay_directory,
    replay_fixture,
)


def cmd_replay(args: argparse.Namespace) -> int:
    fd = Path(args.fixture)
    if not fd.exists():
        print(f"ERROR: fixture not found: {fd}", file=sys.stderr)
        return 2
    try:
        fixture = load_fixture(fd)
    except ValueError as e:
        print(f"ERROR: invalid fixture: {e}", file=sys.stderr)
        return 2
    result = replay_fixture(
        fixture,
        models=tuple(args.models.split(",")) if args.models else DEFAULT_MODEL_CHAIN,
        timeout_s=args.timeout,
    )
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.status == "PASS" else 1


def cmd_replay_all(args: argparse.Namespace) -> int:
    root = Path(args.fixtures_root)
    if not root.exists():
        print(f"ERROR: fixtures root not found: {root}", file=sys.stderr)
        return 2
    results = replay_directory(
        root,
        models=tuple(args.models.split(",")) if args.models else DEFAULT_MODEL_CHAIN,
        timeout_s=args.timeout,
    )
    summary = {
        "total": len(results),
        "passed": sum(1 for r in results if r.status == "PASS"),
        "failed": sum(1 for r in results if r.status not in ("PASS",)),
        "results": [r.to_dict() for r in results],
    }
    print(json.dumps(summary, indent=2))
    return 0 if summary["failed"] == 0 and summary["total"] > 0 else 1


def cmd_check_env(args: argparse.Namespace) -> int:
    avail = gemini_available()
    print(json.dumps({
        "gemini_available": avail,
        "default_model_chain": list(DEFAULT_MODEL_CHAIN),
        "default_timeout_s": DEFAULT_TIMEOUT_S,
        "ready": avail,
    }, indent=2))
    return 0 if avail else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jsc-ai-prompt-regression",
        description="Replay AI prompts + validate JSON output against JsonExtractor schema",
    )
    sub = parser.add_subparsers(dest="cmd")

    p1 = sub.add_parser("replay", help="Replay one fixture dir")
    p1.add_argument("fixture", help="Path to fixture dir (containing spec.json + prompt.txt + input.txt)")
    p1.add_argument("--models", default="", help="Comma-separated model chain override")
    p1.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    p1.set_defaults(func=cmd_replay)

    p2 = sub.add_parser("replay-all", help="Replay every fixture in a root dir")
    p2.add_argument("fixtures_root", help="Path to fixtures root (one subdir per fixture)")
    p2.add_argument("--models", default="")
    p2.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    p2.set_defaults(func=cmd_replay_all)

    p3 = sub.add_parser("check-env", help="Check whether gemini CLI is installed + reachable")
    p3.set_defaults(func=cmd_check_env)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
