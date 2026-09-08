"""Correlate frames to transcript segments by timestamp overlap."""
from __future__ import annotations
from .extract_frames import Frame
from .parse_transcript import Segment


def correlate(frames: list[Frame], segments: list[Segment]) -> list[dict]:
    result = []
    last_frame_id = None

    for seg in segments:
        matched_ids = []
        for f in frames:
            if seg.start_seconds <= f.timestamp_seconds <= seg.end_seconds:
                matched_ids.append(f.id)

        if not matched_ids and last_frame_id is not None:
            matched_ids = [last_frame_id]

        if matched_ids:
            last_frame_id = matched_ids[-1]

        result.append({
            "start_seconds": seg.start_seconds,
            "end_seconds": seg.end_seconds,
            "speaker": seg.speaker,
            "text": seg.text,
            "frame_ids": matched_ids,
        })

    return result
