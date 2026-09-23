"""CLI entry point for meeting processor."""
from __future__ import annotations
from jsc_common.workspace import state_dir
import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .extract_frames import extract_frames, SENSITIVITY_PRESETS
from .parse_transcript import parse_transcript
from .correlate import correlate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Adaptive frame extraction from meeting recordings with transcript correlation.",
    )
    parser.add_argument("--video", required=True, help="Path to video file")
    parser.add_argument("--transcript", help="Path to transcript file (VTT, SRT, or Zoom plaintext)")
    parser.add_argument("--output", help="Output directory (default: selected client state/meetings; required without a selected workspace)")
    parser.add_argument("--sample-interval", type=int, default=10,
                        help="Pass 1: extract one frame every N seconds (default: 10)")
    parser.add_argument("--sensitivity", choices=["low", "medium", "high"],
                        help="Sensitivity preset: low (0.08/0.04), medium (0.05/0.02, default), high (0.03/0.01). "
                             "Overrides --diff-high and --diff-low if provided.")
    parser.add_argument("--diff-high", type=float, default=0.05,
                        help="Pixel diff threshold to always keep a frame (default: 0.05)")
    parser.add_argument("--diff-low", type=float, default=0.02,
                        help="Pixel diff below this = static, drop unless min-interval (default: 0.02)")
    parser.add_argument("--diff-mid-max-gap", type=int, default=60,
                        help="For medium-diff frames, keep if gap exceeds this many seconds (default: 60)")
    parser.add_argument("--min-interval", type=int, default=180,
                        help="Maximum gap before forcing a frame even on static content (default: 180)")
    parser.add_argument("--analyze", action="store_true", default=False,
                        help="Analyze each frame via Gemini CLI for visual descriptions (requires gemini on PATH)")

    args = parser.parse_args(argv)

    # Apply sensitivity preset if provided (overrides diff_high and diff_low)
    if args.sensitivity:
        args.diff_high, args.diff_low = SENSITIVITY_PRESETS[args.sensitivity]

    video_path = Path(args.video).resolve()
    if not video_path.exists():
        print(f"Error: video file not found: {video_path}", file=sys.stderr)
        return 1

    if not shutil.which("ffmpeg"):
        print("Error: ffmpeg not found on PATH", file=sys.stderr)
        return 1

    if args.output:
        output_dir = Path(args.output)
    else:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = state_dir("meetings") / f"meeting-{ts}"

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Processing {video_path.name}...")
    print(f"  Pass 1: sampling every {args.sample_interval}s")
    print(f"  Pass 2: adaptive diff filter (high={args.diff_high}, low={args.diff_low}, mid-gap={args.diff_mid_max_gap}s, min-interval={args.min_interval}s)")

    result = extract_frames(
        video_path=str(video_path),
        output_dir=str(output_dir),
        sample_interval=args.sample_interval,
        diff_high=args.diff_high,
        diff_low=args.diff_low,
        diff_mid_max_gap=args.diff_mid_max_gap,
        min_interval=args.min_interval,
    )

    print(f"  Sampled {result.total_sampled} frames → kept {len(result.frames)} "
          f"({result.kept_by_diff} by visual change, {result.kept_by_min_interval} by min-interval, "
          f"{result.dropped_static} dropped as static, {result.scroll_collapsed} scroll-collapsed)")
    print(f"  Processing time: {result.processing_time_seconds:.1f}s")

    transcript_segments = []
    transcript_format = None
    transcript_path = None

    if args.transcript:
        transcript_path = Path(args.transcript).resolve()
        if not transcript_path.exists():
            print(f"Warning: transcript file not found: {transcript_path}", file=sys.stderr)
        else:
            content = transcript_path.read_text(encoding="utf-8", errors="replace")
            transcript_format, segments = parse_transcript(content, result.duration_seconds)
            print(f"  {len(segments)} transcript segments parsed (format: {transcript_format})")
            transcript_segments = correlate(result.frames, segments)

    ffmpeg_version = ""
    try:
        import subprocess
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        m = __import__("re").search(r"ffmpeg version (\S+)", r.stdout)
        if m:
            ffmpeg_version = m.group(1)
    except Exception:
        pass

    # ── Optional Gemini analysis ──
    analyzed_with_gemini = False
    frame_descriptions: dict[str, str] = {}  # frame_id → description

    if args.analyze:
        gemini_path = shutil.which("gemini")
        if not gemini_path:
            print("Warning: --analyze requested but 'gemini' CLI not found on PATH. Skipping analysis.",
                  file=sys.stderr)
        elif len(result.frames) == 0:
            print("  No frames to analyze.", file=sys.stderr)
        else:
            analyzed_with_gemini = True
            total_frames = len(result.frames)
            for idx, f in enumerate(result.frames, 1):
                frame_path = str(output_dir / f.path)
                print(f"  Analyzing frame {idx}/{total_frames} via Gemini...", file=sys.stderr)
                try:
                    gemini_result = subprocess.run(
                        [
                            gemini_path, "-p",
                            "Describe what is shown in this meeting screenshot in 1-2 sentences. "
                            "Focus on: slide content, screen-shared application, UI elements, or text visible. "
                            "If it's just a webcam view of a person, say 'Webcam view of [person name if visible].' "
                            f"@{frame_path}",
                        ],
                        capture_output=True, text=True, timeout=60,
                        stdin=subprocess.DEVNULL,
                    )
                    desc = gemini_result.stdout.strip() if gemini_result.returncode == 0 else "(analysis failed)"
                    if not desc:
                        desc = "(analysis failed)"
                except Exception:
                    desc = "(analysis failed)"
                frame_descriptions[f.id] = desc
                # Rate limiting: 2-second delay between calls to avoid free-tier limits
                if idx < total_frames:
                    time.sleep(2)
            print(f"  Analyzed {total_frames} frames via Gemini.", file=sys.stderr)

    timeline = {
        "meeting": {
            "source_video": str(video_path),
            "source_transcript": str(transcript_path) if transcript_path else None,
            "duration_seconds": result.duration_seconds,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        },
        "frames": [
            {
                "id": f.id,
                "timestamp_seconds": f.timestamp_seconds,
                "timestamp_display": f.timestamp_display,
                "path": f.path,
                "diff_score": f.diff_score,
                **({"visual_description": frame_descriptions[f.id]} if f.id in frame_descriptions else {}),
            }
            for f in result.frames
        ],
        "transcript_segments": transcript_segments,
    }

    meta = {
        "tool_version": __version__,
        "ffmpeg_version": ffmpeg_version,
        "strategy": "two_pass_adaptive_with_perceptual_dedup",
        "sample_interval": args.sample_interval,
        "diff_high": args.diff_high,
        "diff_low": args.diff_low,
        "diff_mid_max_gap": args.diff_mid_max_gap,
        "min_interval": args.min_interval,
        "sensitivity": args.sensitivity or "custom",
        "total_sampled": result.total_sampled,
        "total_kept": len(result.frames),
        "kept_by_diff": result.kept_by_diff,
        "kept_by_min_interval": result.kept_by_min_interval,
        "dropped_static": result.dropped_static,
        "scroll_collapsed": result.scroll_collapsed,
        "total_transcript_segments": len(transcript_segments),
        "transcript_format_detected": transcript_format,
        "duration_seconds": result.duration_seconds,
        "processing_time_seconds": result.processing_time_seconds,
    }
    if analyzed_with_gemini:
        meta["analyzed_with"] = "gemini"

    timeline_path = output_dir / "timeline.json"
    meta_path = output_dir / "meta.json"

    timeline_path.write_text(json.dumps(timeline, indent=2, ensure_ascii=False), encoding="utf-8")
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nOutput written to {output_dir}/")
    print(f"  timeline.json  ({len(result.frames)} frames, {len(transcript_segments)} segments)")
    print(f"  meta.json")
    print(f"  frames/        ({len(result.frames)} images)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
