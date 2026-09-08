"""Adaptive two-pass frame extraction via ffmpeg with perceptual dedup."""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path

# Pillow is used ONLY in Pass 3 (perceptual dedup of already-extracted JPEGs).
# If unavailable, fall back to the old pixel-diff scroll collapse.
try:
    from PIL import Image as _PILImage
    _PILLOW_AVAILABLE = True
except ImportError:
    _PILImage = None  # type: ignore[assignment]
    _PILLOW_AVAILABLE = False


# Sensitivity presets: (diff_high, diff_low)
SENSITIVITY_PRESETS = {
    "low": (0.08, 0.04),
    "medium": (0.05, 0.02),
    "high": (0.03, 0.01),
}


@dataclass
class Frame:
    id: str
    timestamp_seconds: float
    timestamp_display: str
    path: str
    diff_score: float = 0.0


@dataclass
class ExtractionResult:
    frames: list[Frame] = field(default_factory=list)
    total_sampled: int = 0
    kept_by_diff: int = 0
    kept_by_min_interval: int = 0
    dropped_static: int = 0
    scroll_collapsed: int = 0
    duration_seconds: float = 0.0
    processing_time_seconds: float = 0.0


def _require_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("ffmpeg not found on PATH")
    return path


def _get_duration(video_path: str, ffmpeg: str) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        # Fallback: try co-located with ffmpeg
        candidate = os.path.join(os.path.dirname(ffmpeg), "ffprobe")
        if os.path.isfile(candidate):
            ffprobe = candidate
        else:
            ffprobe = "ffprobe"
    try:
        r = subprocess.run(
            [ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", video_path],
            capture_output=True, text=True, timeout=30,
        )
        info = json.loads(r.stdout)
        return float(info["format"]["duration"])
    except Exception:
        return 0.0


def _ts_display(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:d}h{m:02d}m{s:02d}s"
    return f"{m:02d}m{s:02d}s"


def _frame_filename(seq: int, ts: float) -> str:
    return f"{seq:04d}_{_ts_display(ts)}.jpg"


def _pixel_diff(pixels_a: bytes, pixels_b: bytes, width: int = 320, height: int = 240,
                crop_ratio: float = 0.85) -> float:
    """Mean absolute pixel difference between two raw grayscale buffers, 0.0-1.0.

    Only compares the top-left portion of each frame (controlled by crop_ratio),
    ignoring the bottom-right region where Zoom's self-view webcam overlay typically sits.
    This reduces false positives from face movement in the self-view.
    """
    if not pixels_a or not pixels_b:
        return 1.0
    min_len = min(len(pixels_a), len(pixels_b))
    if min_len == 0:
        return 1.0

    # If the buffer is large enough to be row-major grayscale at the given dimensions,
    # crop to the top-left portion to ignore the webcam overlay region.
    expected_size = width * height
    if min_len >= expected_size and crop_ratio < 1.0:
        crop_h = int(height * crop_ratio)
        crop_w = int(width * crop_ratio)
        total = 0
        count = 0
        for row in range(crop_h):
            row_start = row * width
            for col in range(crop_w):
                idx = row_start + col
                total += abs(pixels_a[idx] - pixels_b[idx])
                count += 1
        if count == 0:
            return 1.0
        return total / (count * 255.0)
    else:
        # Fallback for buffers that don't match expected dimensions
        total = sum(abs(a - b) for a, b in zip(pixels_a[:min_len], pixels_b[:min_len]))
        return total / (min_len * 255.0)


def _dhash(image_path: str, hash_size: int = 8) -> int:
    """Compute a difference hash (dHash) for an image file using Pillow.

    Opens the JPEG, resizes to (hash_size+1, hash_size) grayscale, then computes
    the difference hash: for each pixel, 1 if pixel > right neighbor, 0 otherwise.
    Returns an integer hash (hash_size * hash_size bits).

    Requires Pillow (PIL). Raises RuntimeError if Pillow is not available.
    """
    if not _PILLOW_AVAILABLE:
        raise RuntimeError("Pillow is not available for dHash computation")
    img = _PILImage.open(image_path).convert("L").resize((hash_size + 1, hash_size))
    pixels = list(img.getdata())
    hash_val = 0
    for row in range(hash_size):
        for col in range(hash_size):
            idx = row * (hash_size + 1) + col
            if pixels[idx] > pixels[idx + 1]:
                hash_val |= 1 << (row * hash_size + col)
    return hash_val


def _hamming_distance(hash_a: int, hash_b: int) -> int:
    """Count the number of differing bits between two integer hashes."""
    return bin(hash_a ^ hash_b).count("1")


def _extract_all_raw_pixels(ffmpeg: str, video_path: str, sample_interval: int,
                            width: int = 320, height: int = 240,
                            timeout: int = 600) -> list[bytes]:
    """Extract ALL sampled frames as one raw grayscale pixel stream via a single ffmpeg process.

    Returns a list of raw pixel byte chunks, one per frame.
    Each chunk is width*height bytes of grayscale pixels.
    """
    frame_size = width * height
    cmd = [
        ffmpeg, "-i", video_path,
        "-vf", f"fps=1/{sample_interval},scale={width}:{height}",
        "-pix_fmt", "gray",
        "-f", "rawvideo",
        "pipe:1",
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)

    raw = r.stdout
    frames: list[bytes] = []
    offset = 0
    while offset + frame_size <= len(raw):
        frames.append(raw[offset:offset + frame_size])
        offset += frame_size
    return frames


def extract_frames(
    video_path: str,
    output_dir: str,
    min_interval: int = 180,
    sample_interval: int = 10,
    diff_high: float = 0.05,
    diff_low: float = 0.02,
    diff_mid_max_gap: int = 60,
) -> ExtractionResult:
    """Two-pass adaptive frame extraction with perceptual dedup.

    Pass 1: Sample frames at fixed intervals (sample_interval seconds) — extracts
            JPEGs to a temp directory for final output, AND extracts all frames as
            a single raw grayscale pixel stream for comparison (one ffmpeg process).
    Pass 2: Compare consecutive frames via pixel diff on downscaled grayscale
            (with webcam crop heuristic — ignores bottom-right 15% where Zoom
            self-view typically sits).
      - diff >= diff_high  → keep (meaningful visual change)
      - diff <  diff_low   → drop (static / talking head)
      - diff in between    → keep only if >diff_mid_max_gap seconds since last kept
    Pass 3: Perceptual dedup — for adjacent kept frames, compute their dHash
            (Pillow). If Hamming distance < 5 (out of 64 bits), they're
            perceptually identical — drop the earlier one. Falls back to old
            pixel-diff scroll collapse if Pillow is unavailable.
    Always keep the first frame.
    If no frames survive after pass 2, fall back to fixed-interval gap fills.
    """
    t_start = time.monotonic()
    ffmpeg = _require_ffmpeg()
    video_path = str(Path(video_path).resolve())
    frames_dir = Path(output_dir) / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    duration = _get_duration(video_path, ffmpeg)
    if duration == 0.0:
        print("Warning: could not determine video duration (ffprobe failed or returned 0). "
              "Continuing with extraction.", file=sys.stderr)

    # Scale Pass 1 timeout based on duration
    pass1_timeout = max(120, int(duration * 1.5)) if duration > 0 else 600

    # ── Pass 1: extract JPEG frames to temp dir + raw pixels for comparison ──
    with tempfile.TemporaryDirectory() as tmp:
        # Extract JPEGs for final output
        tmp_pattern = os.path.join(tmp, "sample_%06d.jpg")
        cmd_jpeg = [
            ffmpeg, "-i", video_path,
            "-vf", f"fps=1/{sample_interval}",
            "-q:v", "2",
            tmp_pattern,
        ]
        p1_result = subprocess.run(cmd_jpeg, capture_output=True, text=True, timeout=pass1_timeout)

        # Check ffmpeg return code
        if p1_result.returncode != 0:
            sample_files = sorted(Path(tmp).glob("sample_*.jpg"))
            if len(sample_files) == 0:
                stderr_snippet = (p1_result.stderr or "")[:500]
                raise RuntimeError(
                    f"ffmpeg Pass 1 failed with exit code {p1_result.returncode} "
                    f"and produced 0 frames. stderr: {stderr_snippet}"
                )
            # Non-zero but some frames produced — warn and continue
            print(f"Warning: ffmpeg Pass 1 exited with code {p1_result.returncode} "
                  f"but produced {len(sample_files)} frames. Continuing.", file=sys.stderr)

        sample_files = sorted(Path(tmp).glob("sample_*.jpg"))
        total_sampled = len(sample_files)

        if total_sampled == 0:
            if duration > 0:
                raise RuntimeError(
                    f"Video has duration {duration:.1f}s but ffmpeg produced 0 sampled frames. "
                    "The video may be corrupt or in an unsupported format."
                )
            elapsed = time.monotonic() - t_start
            return ExtractionResult(duration_seconds=round(duration, 2),
                                    processing_time_seconds=round(elapsed, 2))

        # Extract raw grayscale pixels via single ffmpeg process for diff comparison
        print(f"  Pass 2: comparing {total_sampled} frames...", file=sys.stderr)
        raw_pixels = _extract_all_raw_pixels(
            ffmpeg, video_path, sample_interval, timeout=pass1_timeout
        )

        # If raw pixel extraction returned fewer frames than JPEG extraction,
        # pad with empty bytes so indices align; if more, truncate
        while len(raw_pixels) < total_sampled:
            raw_pixels.append(b"")
        raw_pixels = raw_pixels[:total_sampled]

        # ── Pass 2: adaptive diff-based filtering ──
        kept: list[tuple[float, Path, float, bytes]] = []  # (timestamp, path, diff_score, pixels)
        last_kept_ts = -999.0
        kept_by_diff = 0
        kept_by_min = 0
        dropped = 0

        for i, fp in enumerate(sample_files):
            ts = i * sample_interval
            cur_pixels = raw_pixels[i]

            if i == 0:
                # Always keep first frame
                kept.append((ts, fp, 0.0, cur_pixels))
                last_kept_ts = ts
                kept_by_diff += 1
                continue

            prev_pixels = raw_pixels[i - 1]
            diff = _pixel_diff(prev_pixels, cur_pixels, crop_ratio=0.85)

            if diff >= diff_high:
                kept.append((ts, fp, diff, cur_pixels))
                last_kept_ts = ts
                kept_by_diff += 1
            elif diff >= diff_low and (ts - last_kept_ts) >= diff_mid_max_gap:
                kept.append((ts, fp, diff, cur_pixels))
                last_kept_ts = ts
                kept_by_min += 1
            elif diff < diff_low and (ts - last_kept_ts) >= min_interval:
                # Safety net: even static content gets a frame every min_interval
                kept.append((ts, fp, diff, cur_pixels))
                last_kept_ts = ts
                kept_by_min += 1
            else:
                dropped += 1

            # Progress reporting every 50 frames
            if (i + 1) % 50 == 0:
                print(f"  Processing frame {i + 1}/{total_sampled}...", file=sys.stderr)

        # ── Pass 3: perceptual dedup (or pixel-diff fallback) ──
        scroll_collapsed = 0
        if len(kept) > 1 and _PILLOW_AVAILABLE:
            # Perceptual hash dedup: compute dHash for each kept frame's JPEG,
            # then drop the earlier frame in any adjacent pair with Hamming distance < 5.
            collapse_mask = [False] * len(kept)
            dhashes: list[int | None] = []
            for _ts, fp, _diff, _px in kept:
                try:
                    dhashes.append(_dhash(str(fp)))
                except Exception:
                    dhashes.append(None)
            for j in range(len(kept) - 1):
                h_a = dhashes[j]
                h_b = dhashes[j + 1]
                if h_a is not None and h_b is not None:
                    if _hamming_distance(h_a, h_b) < 5:
                        collapse_mask[j] = True
                        scroll_collapsed += 1
            if scroll_collapsed > 0:
                kept = [k for k, masked in zip(kept, collapse_mask) if not masked]
        elif len(kept) > 1:
            # Fallback: old pixel-diff scroll collapse (Pillow not available)
            if not _PILLOW_AVAILABLE:
                warnings.warn(
                    "Pillow not available — falling back to pixel-diff scroll collapse. "
                    "Install Pillow for stronger perceptual dedup.",
                    stacklevel=2,
                )
            collapse_mask = [False] * len(kept)
            for j in range(len(kept) - 1):
                ts_a, _, _, px_a = kept[j]
                ts_b, _, _, px_b = kept[j + 1]
                if (ts_b - ts_a) <= 30 and px_a and px_b:
                    pairwise_diff = _pixel_diff(px_a, px_b)
                    if pairwise_diff < 0.04:
                        collapse_mask[j] = True
                        scroll_collapsed += 1
            if scroll_collapsed > 0:
                kept = [k for k, masked in zip(kept, collapse_mask) if not masked]

        # ── Copy kept frames to output ──
        frames: list[Frame] = []
        for seq, (ts, src, diff_score, _px) in enumerate(kept, 1):
            fname = _frame_filename(seq, ts)
            dest = frames_dir / fname
            shutil.copy2(str(src), str(dest))
            frames.append(Frame(
                id=f"{seq:04d}",
                timestamp_seconds=round(ts, 2),
                timestamp_display=_ts_display(ts),
                path=f"frames/{fname}",
                diff_score=round(diff_score, 4),
            ))

    elapsed = time.monotonic() - t_start
    return ExtractionResult(
        frames=frames,
        total_sampled=total_sampled,
        kept_by_diff=kept_by_diff,
        kept_by_min_interval=kept_by_min,
        dropped_static=dropped,
        scroll_collapsed=scroll_collapsed,
        duration_seconds=round(duration, 2),
        processing_time_seconds=round(elapsed, 2),
    )
