"""Multi-format transcript parser (VTT, SRT, Zoom plaintext)."""
from __future__ import annotations
import re
from dataclasses import dataclass


@dataclass
class Segment:
    start_seconds: float
    end_seconds: float
    speaker: str
    text: str


def detect_format(content: str) -> str:
    lines = content.strip().splitlines()
    if not lines:
        return "unknown"
    first = lines[0].strip()
    if first.startswith("WEBVTT"):
        return "vtt"
    if first.isdigit() and len(lines) > 1 and "-->" in lines[1]:
        return "srt"
    if re.match(r"^\d+:\d+", first):
        return "zoom_plaintext"
    for line in lines[:5]:
        if re.match(r"^\d+:\d+", line.strip()):
            return "zoom_plaintext"
    return "unknown"


def _parse_ts_to_seconds(ts: str) -> float:
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


def _parse_vtt(content: str) -> list[Segment]:
    segments: list[Segment] = []
    blocks = re.split(r"\n\n+", content.strip())
    for block in blocks:
        lines = block.strip().splitlines()
        if not lines:
            continue
        ts_line = None
        text_lines = []
        for i, line in enumerate(lines):
            if "-->" in line:
                ts_line = line
                text_lines = lines[i + 1:]
                break
        if not ts_line:
            continue
        parts = ts_line.split("-->")
        start = _parse_ts_to_seconds(parts[0])
        end = _parse_ts_to_seconds(parts[1].split()[0])
        raw_text = " ".join(text_lines).strip()
        speaker = ""
        v_match = re.match(r"<v\s+([^>]+)>(.+?)(?:</v>)?$", raw_text)
        if v_match:
            speaker = v_match.group(1).strip()
            raw_text = v_match.group(2).strip()
        else:
            sp_match = re.match(r"^([^:]{2,30}):\s*(.+)$", raw_text)
            if sp_match:
                speaker = sp_match.group(1).strip()
                raw_text = sp_match.group(2).strip()
        raw_text = re.sub(r"<[^>]+>", "", raw_text).strip()
        if raw_text:
            segments.append(Segment(start, end, speaker, raw_text))
    return segments


def _parse_srt(content: str) -> list[Segment]:
    segments: list[Segment] = []
    blocks = re.split(r"\n\n+", content.strip())
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 2:
            continue
        ts_idx = None
        for i, line in enumerate(lines):
            if "-->" in line:
                ts_idx = i
                break
        if ts_idx is None:
            continue
        parts = lines[ts_idx].split("-->")
        start = _parse_ts_to_seconds(parts[0])
        end = _parse_ts_to_seconds(parts[1].split()[0])
        text_lines = lines[ts_idx + 1:]
        raw_text = " ".join(text_lines).strip()
        speaker = ""
        sp_match = re.match(r"^([^:]{2,30}):\s*(.+)$", raw_text)
        if sp_match:
            speaker = sp_match.group(1).strip()
            raw_text = sp_match.group(2).strip()
        raw_text = re.sub(r"<[^>]+>", "", raw_text).strip()
        if raw_text:
            segments.append(Segment(start, end, speaker, raw_text))
    return segments


_ZOOM_LINE_RE = re.compile(
    r"^(\d+(?::\d+){1,2})\s*-\s*(.+?)(?:\s*-\s*Speaker\s+\d+)?$"
)


def _parse_zoom_plaintext(content: str, fallback_duration: float = 0.0) -> list[Segment]:
    segments: list[Segment] = []
    lines = content.strip().splitlines()

    entries: list[tuple[float, str, list[str]]] = []
    current_ts = 0.0
    current_speaker = ""
    current_lines: list[str] = []
    seen_first_ts = False

    for line in lines:
        m = _ZOOM_LINE_RE.match(line.strip())
        if m:
            if seen_first_ts and current_lines:
                entries.append((current_ts, current_speaker, current_lines))
            seen_first_ts = True
            ts_str = m.group(1)
            current_ts = _parse_ts_to_seconds(ts_str)
            speaker_raw = m.group(2).strip()
            speaker_raw = re.sub(r"\s*-\s*Speaker\s+\d+$", "", speaker_raw).strip()
            current_speaker = speaker_raw
            current_lines = []
        elif seen_first_ts:
            stripped = line.strip()
            if stripped:
                current_lines.append(stripped)

    if seen_first_ts and current_lines:
        entries.append((current_ts, current_speaker, current_lines))

    for i, (ts, speaker, text_lines) in enumerate(entries):
        end = entries[i + 1][0] if i + 1 < len(entries) else (fallback_duration or ts + 30)
        text = " ".join(text_lines)
        if text:
            segments.append(Segment(ts, end, speaker, text))

    return segments


def parse_transcript(content: str, fallback_duration: float = 0.0) -> tuple[str, list[Segment]]:
    fmt = detect_format(content)
    if fmt == "vtt":
        return fmt, _parse_vtt(content)
    if fmt == "srt":
        return fmt, _parse_srt(content)
    if fmt == "zoom_plaintext":
        return fmt, _parse_zoom_plaintext(content, fallback_duration)
    return fmt, []
