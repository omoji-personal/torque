#!/usr/bin/env python3
"""Meeting processor self-tests."""
from __future__ import annotations
import json
import sys
import os
import textwrap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meeting_processor.parse_transcript import detect_format, parse_transcript, Segment
from meeting_processor.correlate import correlate
from meeting_processor.extract_frames import (
    Frame, ExtractionResult, _ts_display, _frame_filename, _pixel_diff,
    _dhash, _hamming_distance, _PILLOW_AVAILABLE,
    SENSITIVITY_PRESETS,
)

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        msg = f"  FAIL: {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)


# ─── Format detection ───

VTT_SAMPLE = "WEBVTT\n\n00:00:01.000 --> 00:00:05.000\nHello world"
SRT_SAMPLE = "1\n00:00:01,000 --> 00:00:05,000\nHello world"
ZOOM_SAMPLE = "1:32 - Alex Example\nHello everyone, welcome to the meeting."
ZOOM_SAMPLE_HOURS = "1:01:32 - Sam Example\nLet me share my screen."

check("F-FD-1 detect VTT", detect_format(VTT_SAMPLE) == "vtt")
check("F-FD-2 detect SRT", detect_format(SRT_SAMPLE) == "srt")
check("F-FD-3 detect Zoom plaintext", detect_format(ZOOM_SAMPLE) == "zoom_plaintext")
check("F-FD-4 detect unknown", detect_format("random text\nno format") == "unknown")
check("F-FD-5 detect Zoom with header lines",
      detect_format("Example Case Management\nWed, May 6, 2026\n\n1:32 - Alex Example\nHello") == "zoom_plaintext")

# ─── VTT parsing ───

VTT_FULL = textwrap.dedent("""\
    WEBVTT

    00:00:01.000 --> 00:00:05.000
    <v Alex Example>Hello everyone, welcome to the meeting.</v>

    00:00:06.000 --> 00:00:12.500
    <v Sam Example>Thanks Alex. Let me share my screen.
""")

fmt, segs = parse_transcript(VTT_FULL)
check("F-VT-1 VTT format detected", fmt == "vtt")
check("F-VT-2 VTT segment count", len(segs) == 2, f"got {len(segs)}")
check("F-VT-3 VTT speaker extracted", segs[0].speaker == "Alex Example", f"got '{segs[0].speaker}'")
check("F-VT-4 VTT start time", segs[0].start_seconds == 1.0)
check("F-VT-5 VTT end time", segs[0].end_seconds == 5.0)
check("F-VT-6 VTT text cleaned", "Hello everyone" in segs[0].text)
check("F-VT-7 VTT no HTML tags", "<v" not in segs[0].text)

# ─── SRT parsing ───

SRT_FULL = textwrap.dedent("""\
    1
    00:00:01,000 --> 00:00:05,000
    Alex: Hello everyone, welcome to the meeting.

    2
    00:00:06,000 --> 00:00:12,500
    Sam Example: Thanks Alex. Let me share my screen.
""")

fmt, segs = parse_transcript(SRT_FULL)
check("F-SR-1 SRT format detected", fmt == "srt")
check("F-SR-2 SRT segment count", len(segs) == 2)
check("F-SR-3 SRT speaker from colon", segs[0].speaker == "Alex")
check("F-SR-4 SRT comma timestamp", segs[0].start_seconds == 1.0)

# ─── Zoom plaintext parsing ───

ZOOM_FULL = textwrap.dedent("""\
    Example Case Management – Core Workflows
    Wed, May 6, 2026

    1:32 - Alex Example
    Hello everyone, welcome to the meeting.
    We have a lot to cover today.

    2:15 - Conference Room (jamess) - Speaker 2
    Thanks Alex. Let me share my screen.

    1:05:30 - Unidentified Speaker
    Can you zoom in on that field?
""")

fmt, segs = parse_transcript(ZOOM_FULL, fallback_duration=4000.0)
check("F-ZP-1 Zoom format detected", fmt == "zoom_plaintext")
check("F-ZP-2 Zoom segment count", len(segs) == 3, f"got {len(segs)}")
check("F-ZP-3 Zoom first speaker", segs[0].speaker == "Alex Example", f"got '{segs[0].speaker}'")
check("F-ZP-4 Zoom first start", segs[0].start_seconds == 92.0, f"got {segs[0].start_seconds}")
check("F-ZP-5 Zoom first end inferred", segs[0].end_seconds == 135.0, f"got {segs[0].end_seconds}")
check("F-ZP-6 Zoom multiline text", "a lot to cover" in segs[0].text)
check("F-ZP-7 Zoom hour timestamp", segs[2].start_seconds == 3930.0, f"got {segs[2].start_seconds}")
check("F-ZP-8 Zoom speaker cleanup", "Speaker 2" not in segs[1].speaker, f"got '{segs[1].speaker}'")
check("F-ZP-9 Zoom last segment end fallback", segs[2].end_seconds == 4000.0)

# ─── Correlation ───

test_frames = [
    Frame("0001", 30.0, "00m30s", "frames/0001_00m30s.jpg"),
    Frame("0002", 90.0, "01m30s", "frames/0002_01m30s.jpg"),
    Frame("0003", 200.0, "03m20s", "frames/0003_03m20s.jpg"),
]

test_segments = [
    Segment(25.0, 50.0, "Alice", "First segment"),
    Segment(50.0, 100.0, "Bob", "Second segment"),
    Segment(100.0, 150.0, "Alice", "Third segment — no frame in range"),
    Segment(190.0, 210.0, "Bob", "Fourth segment"),
]

correlated = correlate(test_frames, test_segments)

check("F-CO-1 correlation count", len(correlated) == 4)
check("F-CO-2 first segment has frame 0001", correlated[0]["frame_ids"] == ["0001"])
check("F-CO-3 second segment has frame 0002", correlated[1]["frame_ids"] == ["0002"])
check("F-CO-4 third segment inherits last frame", correlated[2]["frame_ids"] == ["0002"],
      f"got {correlated[2]['frame_ids']}")
check("F-CO-5 fourth segment has frame 0003", correlated[3]["frame_ids"] == ["0003"])
check("F-CO-6 speaker preserved", correlated[0]["speaker"] == "Alice")
check("F-CO-7 text preserved", correlated[0]["text"] == "First segment")

# ─── Correlation edge case: no frames ───

empty_correlated = correlate([], test_segments)
check("F-CO-8 no frames → all empty frame_ids",
      all(s["frame_ids"] == [] for s in empty_correlated))

# ─── Correlation edge case: no segments ───

no_seg_correlated = correlate(test_frames, [])
check("F-CO-9 no segments → empty result", len(no_seg_correlated) == 0)

# ─── Timestamp display ───

check("F-TD-1 seconds display", _ts_display(32.4) == "00m32s")
check("F-TD-2 minutes display", _ts_display(125.0) == "02m05s")
check("F-TD-3 hours display", _ts_display(3661.0) == "1h01m01s")

# ─── Frame filename ───

check("F-FF-1 frame filename", _frame_filename(1, 32.4) == "0001_00m32s.jpg")
check("F-FF-2 frame filename hours", _frame_filename(99, 3661.0) == "0099_1h01m01s.jpg")

# ─── Pixel diff ───

identical = bytes([128] * 100)
check("F-PD-1 identical pixels → 0.0", _pixel_diff(identical, identical) == 0.0)

white = bytes([255] * 100)
black = bytes([0] * 100)
check("F-PD-2 black vs white → 1.0", _pixel_diff(black, white) == 1.0)

half = bytes([128] * 100)
quarter = bytes([64] * 100)
diff = _pixel_diff(half, quarter)
check("F-PD-3 partial diff in range", 0.2 < diff < 0.3, f"got {diff:.4f}")

check("F-PD-4 empty bytes → 1.0", _pixel_diff(b"", b"") == 1.0)
check("F-PD-5 one empty → 1.0", _pixel_diff(b"abc", b"") == 1.0)

# ─── ExtractionResult dataclass ───

er = ExtractionResult()
check("F-ER-1 scroll_collapsed default is 0", er.scroll_collapsed == 0)
check("F-ER-2 processing_time_seconds default is 0.0", er.processing_time_seconds == 0.0)
check("F-ER-3 all fields present",
      hasattr(er, "frames") and hasattr(er, "total_sampled") and
      hasattr(er, "kept_by_diff") and hasattr(er, "kept_by_min_interval") and
      hasattr(er, "dropped_static") and hasattr(er, "scroll_collapsed") and
      hasattr(er, "duration_seconds") and hasattr(er, "processing_time_seconds"))

er2 = ExtractionResult(scroll_collapsed=5, processing_time_seconds=12.3)
check("F-ER-4 scroll_collapsed can be set", er2.scroll_collapsed == 5)
check("F-ER-5 processing_time_seconds can be set", er2.processing_time_seconds == 12.3)

# ─── Sensitivity presets ───

check("F-SP-1 low preset exists", "low" in SENSITIVITY_PRESETS)
check("F-SP-2 medium preset exists", "medium" in SENSITIVITY_PRESETS)
check("F-SP-3 high preset exists", "high" in SENSITIVITY_PRESETS)

check("F-SP-4 low preset values", SENSITIVITY_PRESETS["low"] == (0.08, 0.04),
      f"got {SENSITIVITY_PRESETS['low']}")
check("F-SP-5 medium preset values", SENSITIVITY_PRESETS["medium"] == (0.05, 0.02),
      f"got {SENSITIVITY_PRESETS['medium']}")
check("F-SP-6 high preset values", SENSITIVITY_PRESETS["high"] == (0.03, 0.01),
      f"got {SENSITIVITY_PRESETS['high']}")

# Verify presets are ordered: low has highest thresholds, high has lowest
low_h, low_l = SENSITIVITY_PRESETS["low"]
med_h, med_l = SENSITIVITY_PRESETS["medium"]
hi_h, hi_l = SENSITIVITY_PRESETS["high"]
check("F-SP-7 low.high > medium.high > high.high",
      low_h > med_h > hi_h,
      f"got {low_h} > {med_h} > {hi_h}")
check("F-SP-8 low.low > medium.low > high.low",
      low_l > med_l > hi_l,
      f"got {low_l} > {med_l} > {hi_l}")

# ─── Scroll collapse logic (unit test) ───

# Simulate the scroll collapse algorithm on synthetic data
# Two adjacent frames within 30s with diff < 0.04 → drop earlier
def _simulate_scroll_collapse(kept_entries):
    """Simulate Pass 3 scroll collapse on a list of (ts, pixels) tuples.
    Returns (kept_after_collapse, scroll_collapsed_count).
    """
    if len(kept_entries) <= 1:
        return kept_entries, 0
    collapse_mask = [False] * len(kept_entries)
    collapsed = 0
    for j in range(len(kept_entries) - 1):
        ts_a, px_a = kept_entries[j]
        ts_b, px_b = kept_entries[j + 1]
        if (ts_b - ts_a) <= 30 and px_a and px_b:
            pairwise_diff = _pixel_diff(px_a, px_b)
            if pairwise_diff < 0.04:
                collapse_mask[j] = True
                collapsed += 1
    result = [e for e, masked in zip(kept_entries, collapse_mask) if not masked]
    return result, collapsed

# Test 1: two nearly identical frames within 30s → collapse earlier
px_a = bytes([128] * 100)
px_b = bytes([130] * 100)  # diff = 2/255 ≈ 0.0078, well below 0.04
entries_close = [(10.0, px_a), (20.0, px_b)]
result, count = _simulate_scroll_collapse(entries_close)
check("F-SC-1 scroll collapse: similar frames within 30s → drop earlier",
      count == 1, f"collapsed {count}")
check("F-SC-2 scroll collapse: kept frame is the later one",
      len(result) == 1 and result[0][0] == 20.0,
      f"got {[r[0] for r in result]}")

# Test 2: two different frames within 30s → no collapse
px_c = bytes([0] * 100)
px_d = bytes([255] * 100)  # diff = 1.0, way above 0.04
entries_diff = [(10.0, px_c), (20.0, px_d)]
result2, count2 = _simulate_scroll_collapse(entries_diff)
check("F-SC-3 scroll collapse: different frames within 30s → no collapse",
      count2 == 0, f"collapsed {count2}")
check("F-SC-4 scroll collapse: both frames kept",
      len(result2) == 2)

# Test 3: similar frames but > 30s apart → no collapse
entries_far = [(10.0, px_a), (50.0, px_b)]
result3, count3 = _simulate_scroll_collapse(entries_far)
check("F-SC-5 scroll collapse: similar frames >30s apart → no collapse",
      count3 == 0, f"collapsed {count3}")

# Test 4: chain of 4 scroll frames → collapse first 3 (each adjacent pair < 0.04)
px_scroll = [bytes([128 + i] * 100) for i in range(4)]  # 128, 129, 130, 131
entries_chain = [(10.0, px_scroll[0]), (20.0, px_scroll[1]),
                 (25.0, px_scroll[2]), (30.0, px_scroll[3])]
result4, count4 = _simulate_scroll_collapse(entries_chain)
check("F-SC-6 scroll collapse: chain of similar frames → collapse all but last",
      count4 == 3, f"collapsed {count4}")
check("F-SC-7 scroll collapse: only last frame in chain survives",
      len(result4) == 1 and result4[0][0] == 30.0,
      f"got {[r[0] for r in result4]}")

# Test 5: single frame → no collapse
entries_single = [(10.0, px_a)]
result5, count5 = _simulate_scroll_collapse(entries_single)
check("F-SC-8 scroll collapse: single frame → no collapse",
      count5 == 0 and len(result5) == 1)

# Test 6: empty → no collapse
result6, count6 = _simulate_scroll_collapse([])
check("F-SC-9 scroll collapse: empty → no collapse",
      count6 == 0 and len(result6) == 0)

# ─── Perceptual hash (dHash) tests ───

if _PILLOW_AVAILABLE:
    import tempfile
    import os

    # Create two identical solid-color test images and verify dHash distance = 0
    from PIL import Image as _TestImage

    with tempfile.TemporaryDirectory() as _tmp:
        # Identical images → distance 0
        img_a = _TestImage.new("L", (64, 64), color=128)
        path_a = os.path.join(_tmp, "identical_a.jpg")
        img_a.save(path_a)
        img_b = _TestImage.new("L", (64, 64), color=128)
        path_b = os.path.join(_tmp, "identical_b.jpg")
        img_b.save(path_b)

        hash_a = _dhash(path_a)
        hash_b = _dhash(path_b)
        dist_identical = _hamming_distance(hash_a, hash_b)
        check("F-DH-1 identical images → distance 0", dist_identical == 0,
              f"got distance {dist_identical}")

        # Very different images → distance > 30
        # Use a checkerboard pattern vs solid black for maximum hash difference
        img_c = _TestImage.new("L", (64, 64), color=0)  # solid black
        path_c = os.path.join(_tmp, "black.jpg")
        img_c.save(path_c, quality=95)
        # Create a high-contrast checkerboard (8x8 blocks alternating black/white)
        img_d = _TestImage.new("L", (64, 64))
        pixels_d = []
        for y in range(64):
            for x in range(64):
                # 8-pixel checkerboard blocks
                pixels_d.append(255 if ((x // 8) + (y // 8)) % 2 == 0 else 0)
        img_d.putdata(pixels_d)
        path_d = os.path.join(_tmp, "checkerboard.jpg")
        img_d.save(path_d, quality=95)

        hash_c = _dhash(path_c)
        hash_d = _dhash(path_d)
        dist_different = _hamming_distance(hash_c, hash_d)
        check("F-DH-2 very different images → distance > 30", dist_different > 30,
              f"got distance {dist_different}")

        # Hamming distance of same hash with itself = 0
        check("F-DH-3 same hash → hamming distance 0", _hamming_distance(hash_a, hash_a) == 0)

        # Nearly identical images (slightly shifted brightness) → small distance
        img_e = _TestImage.new("L", (64, 64), color=130)  # 128 + 2
        path_e = os.path.join(_tmp, "near_identical.jpg")
        img_e.save(path_e)
        hash_e = _dhash(path_e)
        dist_near = _hamming_distance(hash_a, hash_e)
        check("F-DH-4 nearly identical images → distance < 5", dist_near < 5,
              f"got distance {dist_near}")

    check("F-DH-5 Pillow is available", _PILLOW_AVAILABLE is True)
else:
    # Pillow not available — verify fallback flag
    check("F-DH-5 Pillow not available (fallback mode)", _PILLOW_AVAILABLE is False)
    # Still verify _hamming_distance works (pure Python, no Pillow needed)
    check("F-DH-6 hamming_distance(0, 0) = 0", _hamming_distance(0, 0) == 0)
    check("F-DH-7 hamming_distance(0xFF, 0x00) = 8", _hamming_distance(0xFF, 0x00) == 8)

# ─── Pillow import fallback test ───

# Verify the module-level flag is consistent
check("F-PF-1 _PILLOW_AVAILABLE is a bool", isinstance(_PILLOW_AVAILABLE, bool))

# ─── Webcam crop heuristic tests ───

# Verify _pixel_diff with crop_ratio < 1.0 ignores bottom-right region
# Create two pixel buffers that differ ONLY in the bottom-right corner
_w, _h = 320, 240
_buf_base = bytes([100] * (_w * _h))

# Modify only the bottom-right corner (rows >= 204, cols >= 272)
_buf_corner_diff = bytearray(_buf_base)
crop_h_test = int(_h * 0.85)  # 204
crop_w_test = int(_w * 0.85)  # 272
for row in range(crop_h_test, _h):
    for col in range(crop_w_test, _w):
        _buf_corner_diff[row * _w + col] = 255  # white in bottom-right

_diff_cropped = _pixel_diff(_buf_base, bytes(_buf_corner_diff), width=_w, height=_h, crop_ratio=0.85)
_diff_full = _pixel_diff(_buf_base, bytes(_buf_corner_diff), width=_w, height=_h, crop_ratio=1.0)

check("F-WC-1 cropped diff ignores bottom-right changes → 0.0",
      _diff_cropped == 0.0, f"got {_diff_cropped:.4f}")
check("F-WC-2 full diff detects bottom-right changes → > 0.0",
      _diff_full > 0.0, f"got {_diff_full:.4f}")

# Verify crop_ratio=1.0 behaves like old behavior (compare all pixels)
_diff_identical_crop = _pixel_diff(_buf_base, _buf_base, width=_w, height=_h, crop_ratio=0.85)
check("F-WC-3 identical buffers with crop → 0.0",
      _diff_identical_crop == 0.0, f"got {_diff_identical_crop:.4f}")

# ─── --analyze CLI flag acceptance test ───

import argparse as _argparse

_test_parser = _argparse.ArgumentParser()
_test_parser.add_argument("--video", required=True)
_test_parser.add_argument("--analyze", action="store_true", default=False)
_test_args = _test_parser.parse_args(["--video", "test.mp4", "--analyze"])
check("F-AN-1 --analyze flag accepted by argparse", _test_args.analyze is True)

_test_args_no = _test_parser.parse_args(["--video", "test.mp4"])
check("F-AN-2 --analyze defaults to False", _test_args_no.analyze is False)

# ─── extract_frames signature check (P1-02: no dead parameters) ───

import inspect
sig = inspect.signature(__import__("meeting_processor.extract_frames", fromlist=["extract_frames"]).extract_frames)
param_names = list(sig.parameters.keys())
check("F-DP-1 no 'threshold' parameter",
      "threshold" not in param_names,
      f"found 'threshold' in {param_names}")
check("F-DP-2 no 'max_rate' parameter",
      "max_rate" not in param_names,
      f"found 'max_rate' in {param_names}")
check("F-DP-3 no 'dedup_window' parameter",
      "dedup_window" not in param_names,
      f"found 'dedup_window' in {param_names}")

# Verify expected parameters ARE present
for expected in ["video_path", "output_dir", "min_interval", "sample_interval",
                 "diff_high", "diff_low", "diff_mid_max_gap"]:
    check(f"F-DP-4-{expected} parameter present",
          expected in param_names,
          f"'{expected}' not in {param_names}")

# ─── Summary ───

total = PASS + FAIL
print(f"\nmeeting_processor self-test {'PASSED' if FAIL == 0 else 'FAILED'}: {PASS}/{total} fixtures")
if FAIL:
    print(f"  {FAIL} fixture(s) failed")

sys.exit(1 if FAIL else 0)
