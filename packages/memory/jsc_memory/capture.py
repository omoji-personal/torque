"""Stop-hook synthesis: walk session spool, build candidates, write to L1 review queue.

Per plan-v5: triggers + normalized retry signature + bounded budget.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Optional

from . import scrubber
from . import spool
from . import storage

STOP_SYNTHESIS_BUDGET_SECONDS = 10

# Generated hex digests: never PII, never scrubbed.
_STRUCTURAL_KEYS = ("id", "signature_hash")

# Trigger -> confidence label
TRIGGER_CONFIDENCE = {
    "explicit_lesson": "HIGHEST",
    "failure_then_success": "HIGH",
    "sf_metadata_error": "HIGH",
    "operator_correction": "HIGH",
    "subprocess_timeout": "MEDIUM",
    "sf_dryrun_then_deploy": "MEDIUM",
    "python_traceback": "MEDIUM",
    "hook_block": "MEDIUM",
    "tool_error": "LOW",
    "npm_error": "LOW",
    "openai_rate_limit": "LOW",
    "browser_automation_timeout": "LOW",
}


def _retry_signature(event: dict) -> str:
    """Normalized retry signature per plan-v5: command_family + target_org + metadata_root + error_class."""
    tool = event.get("tool", "unknown")
    cmd = event.get("input", "") if tool == "Bash" else ""
    if not isinstance(cmd, str):
        cmd = str(cmd)
    if tool != "Bash":
        return f"{tool}|{event.get('input_hash', '')}"

    family = cmd.strip().split()[0] if cmd.strip() else "unknown"
    target_match = re.search(r"--target-org\s+(\S+)", cmd)
    target_org = target_match.group(1) if target_match else "none"
    metadata_match = re.search(r"--metadata\s+(\w+):", cmd)
    md_root = metadata_match.group(1) if metadata_match else "none"

    stderr = event.get("stderr_first_line", "") or ""
    if not isinstance(stderr, str):
        stderr = str(stderr)
    error_words = stderr.strip().split()
    error_class = error_words[0] if error_words else "none"
    error_class = re.sub(r"[^A-Za-z0-9_]", "", error_class)[:32]

    return f"{family}|{target_org}|{md_root}|{error_class}"


def _hash_id(content: str) -> str:
    """Stable id from candidate content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _detect_triggers(events: list[dict]) -> list[dict]:
    """Walk events, identify triggers, return list of candidate-seed dicts."""
    candidates = []
    failures_by_sig: dict[str, int] = {}  # signature -> first event index

    for idx, ev in enumerate(events):
        if not isinstance(ev, dict):
            continue
        event_type = ev.get("event_type", "")
        tool = ev.get("tool", "")
        exit_code = ev.get("exit", 0)
        cmd = ev.get("input", "") if tool == "Bash" else ""
        if not isinstance(cmd, str):
            cmd = str(cmd)
        stderr = ev.get("stderr_first_line", "") or ""
        if not isinstance(stderr, str):
            stderr = str(stderr)

        # === explicit operator capture ===
        if event_type == "explicit_lesson":
            candidates.append({"trigger": "explicit_lesson", "idx": idx})
            continue

        # === operator correction ===
        if event_type == "operator_correction":
            candidates.append({"trigger": "operator_correction", "idx": idx})
            continue

        # === tool failure ===
        if event_type == "tool_failure" or (event_type == "tool_call" and exit_code != 0):
            sig = _retry_signature(ev)
            failures_by_sig.setdefault(sig, idx)

            # Specific failure subtypes
            if "sf project deploy" in cmd and re.search(
                r"INVALID_FIELD|INVALID_TYPE|MALFORMED_QUERY", stderr
            ):
                candidates.append({"trigger": "sf_metadata_error", "idx": idx, "signature": sig})
            elif "npm" in cmd and "npm ERR!" in stderr:
                candidates.append({"trigger": "npm_error", "idx": idx, "signature": sig})
            elif stderr.startswith("Traceback"):
                candidates.append({"trigger": "python_traceback", "idx": idx, "signature": sig})
            elif re.search(r"Rate limit|429", stderr):
                candidates.append({"trigger": "openai_rate_limit", "idx": idx, "signature": sig})
            elif tool.startswith("mcp__claude-in-chrome__"):
                candidates.append({"trigger": "browser_automation_timeout", "idx": idx, "signature": sig})
            else:
                candidates.append({"trigger": "tool_error", "idx": idx, "signature": sig})
            continue

        # === successful tool — check for failure-then-success ===
        if (event_type in ("tool_call", "tool_success") and exit_code == 0):
            sig = _retry_signature(ev)
            if sig in failures_by_sig:
                fail_idx = failures_by_sig[sig]
                # Filter out transient retry: must be ≥5 events OR ≥5min apart with different input
                fail_event = events[fail_idx]
                fail_input_hash = fail_event.get("input_hash", "")
                cur_input_hash = ev.get("input_hash", "")
                same_input = fail_input_hash == cur_input_hash
                fail_ts = fail_event.get("ts", 0)
                cur_ts = ev.get("ts", 0)
                if isinstance(fail_ts, str):
                    try:
                        from datetime import datetime
                        fail_ts = int(datetime.fromisoformat(fail_ts.replace("Z", "+00:00")).timestamp())
                    except Exception:
                        fail_ts = 0
                if isinstance(cur_ts, str):
                    try:
                        from datetime import datetime
                        cur_ts = int(datetime.fromisoformat(cur_ts.replace("Z", "+00:00")).timestamp())
                    except Exception:
                        cur_ts = 0

                event_distance = idx - fail_idx
                time_distance = cur_ts - fail_ts if (cur_ts and fail_ts) else 0

                # Real F-then-S: input changed (operator fixed it) OR significant gap
                if (not same_input) or event_distance >= 5 or time_distance >= 300:
                    candidates.append({
                        "trigger": "failure_then_success",
                        "idx": idx,
                        "fail_idx": fail_idx,
                        "signature": sig,
                    })
                    # Pop so we don't re-trigger on next success
                    failures_by_sig.pop(sig, None)
            # Also detect dry-run-then-deploy
            if "--dry-run" in cmd:
                # Look ahead for the same command without --dry-run
                cmd_without = cmd.replace("--dry-run", "").strip()
                for fwd_idx, fwd_ev in enumerate(events[idx + 1:], start=idx + 1):
                    fwd_cmd = fwd_ev.get("input", "") if isinstance(fwd_ev, dict) else ""
                    if isinstance(fwd_cmd, str) and fwd_cmd.replace("--dry-run", "").strip() == cmd_without:
                        candidates.append({
                            "trigger": "sf_dryrun_then_deploy",
                            "idx": fwd_idx,
                            "dryrun_idx": idx,
                        })
                        break

    # Deduplicate candidates by signature (keep first occurrence)
    seen_sigs = set()
    deduped = []
    for c in candidates:
        sig = c.get("signature", c.get("trigger") + str(c["idx"]))
        if sig in seen_sigs:
            continue
        seen_sigs.add(sig)
        deduped.append(c)
    return deduped


def _build_candidate(events: list[dict], cand_seed: dict) -> dict:
    """Build a Lesson-like candidate dict from a seed + window of events."""
    idx = cand_seed["idx"]
    window_start = max(0, idx - 5)
    window_end = min(len(events), idx + 3)
    window = events[window_start:window_end]
    trigger = cand_seed["trigger"]
    confidence = TRIGGER_CONFIDENCE.get(trigger, "LOW")

    # Extract context hints from the trigger event
    trigger_event = events[idx]
    cmd = trigger_event.get("input", "") if trigger_event.get("tool") == "Bash" else ""
    if not isinstance(cmd, str):
        cmd = str(cmd)
    stderr = trigger_event.get("stderr_first_line", "") or ""
    if not isinstance(stderr, str):
        stderr = str(stderr)

    # Generate a short title from trigger + cmd hint
    cmd_words = cmd.strip().split()[:3]
    cmd_hint = " ".join(cmd_words) if cmd_words else trigger
    title = f"[{trigger}] {cmd_hint[:80]}"

    # short = 1-line summary
    short = f"{trigger}: {stderr[:120]}" if stderr else f"{trigger}: {cmd_hint[:120]}"

    # full_text = readable representation of the window
    lines = [f"# Captured: {trigger} (confidence: {confidence})", ""]
    for w_ev in window:
        if not isinstance(w_ev, dict):
            continue
        tool = w_ev.get("tool", "?")
        et = w_ev.get("event_type", "tool_call")
        ec = w_ev.get("exit", 0)
        inp = w_ev.get("input", "")
        if isinstance(inp, str) and len(inp) > 200:
            inp = inp[:200] + "..."
        line = f"- [{et}] {tool}: exit={ec} | input={inp}"
        lines.append(line)
        if w_ev.get("stderr_first_line"):
            stderr_excerpt = w_ev["stderr_first_line"]
            if isinstance(stderr_excerpt, str) and len(stderr_excerpt) > 200:
                stderr_excerpt = stderr_excerpt[:200] + "..."
            lines.append(f"    stderr: {stderr_excerpt}")
    full_text = "\n".join(lines)

    # Signature keywords for relevance match
    keywords = []
    if cmd:
        keywords.extend(re.findall(r"[a-zA-Z][\w-]+", cmd)[:10])
    if stderr:
        keywords.extend(re.findall(r"[a-zA-Z][\w-]+", stderr)[:10])
    keywords = list({k.lower() for k in keywords})[:20]  # dedupe + cap

    # Client scope hint (any sf-* alias mention)
    client_scope = []
    for ev in window:
        if not isinstance(ev, dict):
            continue
        text = (ev.get("input", "") or "") + " " + (ev.get("stderr_first_line", "") or "")
        if not isinstance(text, str):
            continue
        client_match = re.findall(r"sf-([a-z][\w-]+)", text)
        client_scope.extend(client_match)
    client_scope = list({c for c in client_scope})[:5]

    signature_hash = hashlib.sha256(
        (cand_seed.get("signature", trigger) + cmd_hint).encode("utf-8")
    ).hexdigest()[:16]

    candidate_id = _hash_id(title + str(idx) + str(time.time()))

    return {
        "id": candidate_id,
        "title": title,
        "short": short,
        "full_text": full_text,
        "confidence": confidence,
        "trigger": trigger,
        "captured_at": int(time.time()),
        "last_seen": int(time.time()),
        "state": "review_pending",
        "helpful_count": 0,
        "stale_count": 0,
        "surfacing_attempts": 0,
        "missed_review_count": 0,
        "client_scope": client_scope,
        "signature_keywords": keywords,
        "signature_hash": signature_hash,
    }


def synthesize_session(session_id: str) -> tuple[int, Optional[str]]:
    """Read spool, build candidates, scrub, write to L1. Returns (count, drop_reason | None).

    Bounded by STOP_SYNTHESIS_BUDGET_SECONDS. On exceed: writes what's done +
    logs synthesis_truncated (caller's responsibility to log to audit log).

    Per audit codex-R2-P1-18: deadline now enforced BEFORE candidate emission
    so a giant spool can't burn the entire budget on read alone with nothing
    left for actual lesson capture.
    """
    deadline = time.monotonic() + STOP_SYNTHESIS_BUDGET_SECONDS
    events = spool.read_events(session_id)
    if not events:
        return 0, None
    if time.monotonic() > deadline:
        return 0, "synthesis_truncated_at_read"

    seeds = _detect_triggers(events)
    if not seeds:
        return 0, None
    if time.monotonic() > deadline:
        return 0, "synthesis_truncated_at_trigger_detect"

    written = 0
    truncated = False
    for seed in seeds:
        if time.monotonic() > deadline:
            truncated = True
            break
        try:
            candidate = _build_candidate(events, seed)
            scrubbed, drop_reason = scrubber.scrub_candidate(candidate)
            if drop_reason:
                continue
            # id and signature_hash are our own sha256 hex digests, not captured
            # content. The value scrubber can mistake them for Salesforce ids
            # (e.g. any 16-hex id starting with "a") and rewrite them to
            # "<sf_id>", which corrupts the id and, on Windows, makes the L1
            # filename illegal so the write fails and is silently skipped.
            for key in _STRUCTURAL_KEYS:
                scrubbed[key] = candidate[key]
            lesson = storage.Lesson.from_dict(scrubbed)
            storage.write_review_candidate(lesson)
            written += 1
        except Exception:
            # Fail-open per privacy-and-logging discipline
            continue

    return written, ("synthesis_truncated" if truncated else None)


def synthesize_orphan(spool_path) -> tuple[int, Optional[str]]:
    """Recover an orphaned spool. Caller must hold the lease.

    Returns (count, drop_reason | None). Spool path is the absolute spool file.
    """
    try:
        # Derive session_id from filename
        session_id = spool_path.stem
        return synthesize_session(session_id)
    except Exception as e:
        return 0, f"orphan_recovery_exception: {type(e).__name__}"
