"""post_deploy_polling.py — background polling for async sf operations.

Phase I.4-extended-2 (v7.17.0). Some sf operations are async (bulk jobs,
multi-hour deploys, package installs). The wrapper records `deploy_id` /
`bulk_job_id` in the snapshot manifest at submission time, but the post-finalize
phase can only run if the operator waits synchronously. This module provides:

1. **Queue model**: a JSON queue at $JSC_REVERT_DIR/_polling_queue/ that holds
   "this snapshot is waiting on this job id" entries.
2. **Poll worker**: a daemonizable poller that iterates the queue, runs
   `sf project deploy report --job-id <id> --json` (or sf bulk equivalent),
   updates the snapshot manifest with terminal status when the job completes,
   removes from queue, and writes a synthesis line to the audit log.
3. **Operator-friendly invocations**:
   - `jsc post-deploy poll` — one-shot pass; exits 0 when queue is empty
   - `jsc post-deploy daemon` — loops until SIGINT/SIGTERM; useful with
     `launchd` / `systemd` / `nohup`

Out of scope (operator may set this up themselves):
- launchd plist for macOS auto-start
- systemd unit file for Linux
- CI / cron registration

Design notes:
- Queue entries are append-only JSONL; consumed entries are tombstoned (not
  deleted) so a restart can replay safely
- Locking: a per-queue-file lockfile prevents two daemons from racing
- Backoff: per-entry retry counter + exponential delay (60s → 5min → 30min);
  giving up after MAX_RETRIES marks the snapshot as `polling_exhausted`
"""

from __future__ import annotations
from jsc_common.workspace import state_dir

import argparse
import fcntl
import json
import os
import signal
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from . import bundle, manifest as mf
from .job_outcomes import bulk_outcome, deploy_outcome

# ── Defaults (operator override via env) ─────────────────────────────────────
DEFAULT_QUEUE_DIR = None

def _queue_dir():
    return state_dir("revert", legacy_env="JSC_REVERT_DIR") / "_polling_queue"
DEFAULT_POLL_INTERVAL_S = int(os.environ.get("JSC_REVERT_POLL_INTERVAL_S", "60"))
DEFAULT_MAX_RETRIES = int(os.environ.get("JSC_REVERT_POLL_MAX_RETRIES", "20"))
DEFAULT_DAEMON_TIMEOUT_S = int(os.environ.get("JSC_REVERT_POLL_DAEMON_TIMEOUT_S", "0"))  # 0 = forever

# ── Queue entry shape ────────────────────────────────────────────────────────
# {
#   "queue_entry_id": "<uuid>",
#   "snapshot_id": "<short_hash>",
#   "org_id_short": "00DPP0000004XYZ",
#   "alias": "sample-prod",
#   "operation_type": "data_bulk_update" | "deploy_metadata" | "package_install",
#   "job_id": "750ABC0000XYZ",
#   "sf_report_command": ["sf", "project", "deploy", "report", "--job-id", ...] OR ["sf", "data", "bulk", "report", ...],
#   "submitted_at": "2026-05-15T23:00:00Z",
#   "retry_count": 0,
#   "next_poll_at": "2026-05-15T23:01:00Z",
#   "status": "pending" | "complete" | "failed" | "polling_exhausted" | "tombstoned"
# }

_TOMBSTONE_STATUSES = ("complete", "applied_partial", "failed", "polling_exhausted", "tombstoned")


def enqueue(snapshot_id: str, org_id_short: str, alias: str,
            operation_type: str, job_id: str,
            sf_report_command: list[str],
            queue_dir: Path | None = None) -> str:
    """Add an entry to the polling queue. Returns queue_entry_id."""
    qd = queue_dir or _queue_dir()
    qd.mkdir(parents=True, exist_ok=True, mode=0o700)
    queue_file = qd / "queue.jsonl"
    entry = {
        "queue_entry_id": uuid.uuid4().hex[:16],
        "snapshot_id": snapshot_id,
        "org_id_short": org_id_short,
        "alias": alias,
        "operation_type": operation_type,
        "job_id": job_id,
        "sf_report_command": sf_report_command,
        "submitted_at": _iso_now(),
        "retry_count": 0,
        "next_poll_at": _iso_now(offset_s=DEFAULT_POLL_INTERVAL_S),
        "status": "pending",
    }
    with _queue_lock(qd):
        with queue_file.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    return entry["queue_entry_id"]


def poll_once(queue_dir: Path | None = None) -> dict:
    """Run one pass of the queue. Returns {polled, completed, failed, deferred}."""
    qd = queue_dir or _queue_dir()
    queue_file = qd / "queue.jsonl"
    if not queue_file.exists():
        return {"polled": 0, "completed": 0, "partial": 0, "failed": 0, "deferred": 0, "exhausted": 0}

    stats = {"polled": 0, "completed": 0, "partial": 0, "failed": 0, "deferred": 0, "exhausted": 0}

    with _queue_lock(qd):
        entries = _load_entries(queue_file)
        now = _iso_now()
        for e in entries:
            if e.get("status") in _TOMBSTONE_STATUSES:
                continue
            stats["polled"] += 1
            if e["next_poll_at"] > now:
                stats["deferred"] += 1
                continue
            outcome = _poll_one(e)
            if outcome == "complete":
                e["status"] = "complete"
                stats["completed"] += 1
                _update_snapshot_manifest(e, "complete")
            elif outcome == "applied_partial":
                e["status"] = "applied_partial"
                stats["partial"] += 1
                _update_snapshot_manifest(e, "applied_partial")
            elif outcome == "failed":
                e["status"] = "failed"
                stats["failed"] += 1
                _update_snapshot_manifest(e, "failed")
            elif outcome == "pending":
                e["retry_count"] += 1
                if e["retry_count"] >= DEFAULT_MAX_RETRIES:
                    e["status"] = "polling_exhausted"
                    stats["exhausted"] += 1
                    _update_snapshot_manifest(e, "polling_exhausted")
                else:
                    delay = _exp_backoff(e["retry_count"])
                    e["next_poll_at"] = _iso_now(offset_s=delay)

        # Atomic rewrite of queue.jsonl with updated entries
        _rewrite_queue(queue_file, entries)

    return stats


def run_daemon(queue_dir: Path | None = None,
               poll_interval_s: int = DEFAULT_POLL_INTERVAL_S,
               timeout_s: int = DEFAULT_DAEMON_TIMEOUT_S) -> int:
    """Loop poll_once until SIGINT/SIGTERM or timeout. Returns exit code."""
    qd = queue_dir or _queue_dir()
    qd.mkdir(parents=True, exist_ok=True, mode=0o700)

    stop = {"flag": False}
    def _handler(signum, frame):
        stop["flag"] = True
    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)

    started = time.monotonic()
    while not stop["flag"]:
        stats = poll_once(qd)
        if stats["polled"] > 0 or stats["completed"] + stats["failed"] + stats["exhausted"] > 0:
            print(f"[post-deploy-poll] {stats}", flush=True)
        if timeout_s and (time.monotonic() - started) > timeout_s:
            print("[post-deploy-poll] timeout reached, exiting", flush=True)
            break
        # Sleep poll_interval_s but wake on signal
        for _ in range(poll_interval_s):
            if stop["flag"]:
                break
            time.sleep(1)

    return 0


# ── Internals ────────────────────────────────────────────────────────────────


def _iso_now(offset_s: int = 0) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_s)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _queue_lock(queue_dir: Path):
    """Context manager for per-queue-dir advisory lock to prevent two daemons racing."""
    lock_path = queue_dir / ".lock"

    class _LockCtx:
        def __init__(self, path):
            self.path = path
            self.fd = None
        def __enter__(self):
            self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(self.fd, fcntl.LOCK_EX)
            return self
        def __exit__(self, *a):
            if self.fd is not None:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
                os.close(self.fd)
    return _LockCtx(lock_path)


def _load_entries(queue_file: Path) -> list[dict]:
    entries = []
    if not queue_file.exists():
        return entries
    with queue_file.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # skip malformed
    return entries


def _rewrite_queue(queue_file: Path, entries: list[dict]):
    tmp = queue_file.with_suffix(".jsonl.tmp")
    with tmp.open("w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    tmp.replace(queue_file)


def _poll_one(entry: dict) -> str:
    """Observe one exact job. Missing evidence remains pending; never resubmit.

    Resume/report may exit nonzero after a partial bulk write, so classification
    uses its structured result. Store each observation beside the private snapshot.
    """
    import subprocess
    try:
        snap_dir, _ = mf.load_by_id(entry["org_id_short"], entry["alias"], entry["snapshot_id"])
    except (FileNotFoundError, ValueError, KeyError):
        return "pending"  # No private evidence destination: do not launch a report.

    def observe(command):
        try:
            proc = subprocess.run(command, capture_output=True, text=True,
                                  timeout=60, cwd=snap_dir)
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            if snap_dir:
                artifact = snap_dir / ("poll-" + uuid.uuid4().hex + ".json")
                decode = lambda value: value.decode(errors="replace") if isinstance(value, bytes) else value
                bundle.atomic_write_json(artifact, {"command": command, "error": str(exc),
                    "stdout": decode(getattr(exc, "stdout", None)), "stderr": decode(getattr(exc, "stderr", None))})
                entry.setdefault("observation_paths", []).append(str(artifact))
            return None, None
        stdout = proc.stdout
        try:
            parsed = json.loads(stdout)
            result = parsed.get("result") if isinstance(parsed, dict) else None
            if isinstance(result, dict) and "statusCode" in result and "headers" in result:
                parsed = {**parsed, "result": {key: value for key, value in result.items() if key != "headers"}}
                stdout = json.dumps(parsed)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if snap_dir:
            artifact = snap_dir / ("poll-" + uuid.uuid4().hex + ".json")
            bundle.atomic_write_json(artifact, {"command": command, "exit_code": proc.returncode,
                                               "stdout": stdout, "stderr": proc.stderr})
            entry.setdefault("observation_paths", []).append(str(artifact))
        return parsed, proc.returncode

    data, code = observe(entry["sf_report_command"])
    if not isinstance(data, dict):
        return "pending"
    op_type = entry.get("operation_type", "")
    if op_type.startswith("data_bulk_"):
        status, job_id = bulk_outcome(data, code, entry.get("job_id"))
        # Current CLI errors retain JobComplete but omit counts. Results is a
        # read-only lookup; it writes CSVs only inside the located private bundle.
        if status in ("partial", "pending_finalize_required") and job_id and snap_dir:
            result_data, result_code = observe(["sf", "data", "bulk", "results",
                "--target-org", entry["alias"], "--job-id", job_id, "--json"])
            status, _ = bulk_outcome(result_data, result_code, job_id)
            if status in ("partial", "pending_finalize_required"):
                result_data, result_code = observe(["sf", "api", "request", "rest",
                    f"/services/data/v62.0/jobs/ingest/{job_id}", "--method", "GET",
                    "--target-org", entry["alias"], "--json"])
                status, _ = bulk_outcome(result_data, result_code, job_id)
    elif op_type in ("package_install", "package_uninstall"):
        result = data.get("result")
        if not isinstance(result, dict):
            return "pending"
        state = result.get("Status") or result.get("status")
        if state in ("SUCCESS", "Success", "Succeeded") and code == 0:
            return "complete"
        return "failed" if state in ("Failed", "ERROR", "Canceled") else "pending"
    else:
        status, _ = deploy_outcome(data, code, entry.get("job_id"))
    if status in ("complete", "applied_partial", "failed"):
        return status
    return "failed" if status == "abandoned" else "pending"


def _exp_backoff(retry_count: int) -> int:
    """60s → 120s → 240s → ... capped at 30min."""
    return min(60 * (2 ** (retry_count - 1)), 1800)


def _update_snapshot_manifest(entry: dict, final_status: str):
    """Best-effort: load the snapshot manifest, write atomic terminal state.

    Codex-R1-P2-01 closure: update both `post_deploy_polling` block AND the
    top-level `snapshot_status` field when the job reaches a terminal state.
    """
    try:
        snap_dir, m = mf.load_by_id(entry["org_id_short"], entry["alias"],
                                     entry["snapshot_id"])
    except (FileNotFoundError, ValueError):
        return
    m.setdefault("post_deploy_polling", {})
    m["post_deploy_polling"].update({
        "queue_entry_id": entry["queue_entry_id"],
        "job_id": entry["job_id"],
        "final_status": final_status,
        "finalized_at": _iso_now(),
        "retry_count": entry["retry_count"],
        "observation_paths": entry.get("observation_paths", []),
    })
    # Codex-R1-P2-01: update snapshot_status atomically. Map polling final_status
    # to manifest VALID_SNAPSHOT_STATUS.
    status_map = {
        "complete": "complete",
        "applied_partial": "applied_partial",
        "failed": "failed",
        "polling_exhausted": "polling_exhausted",
    }
    new_snapshot_status = status_map.get(final_status)
    if new_snapshot_status:
        # Only overwrite if the wrapper left it as pending_finalize_required
        if m.get("snapshot_status") == "pending_finalize_required":
            m["snapshot_status"] = new_snapshot_status
    mf.save(snap_dir, m)


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jsc post-deploy",
        description="Background polling for async sf operations (Phase I.4-extended-2)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("poll", help="run one polling pass")
    p1.add_argument("--queue-dir", default=None)

    p2 = sub.add_parser("daemon", help="loop until SIGINT/SIGTERM")
    p2.add_argument("--queue-dir", default=None)
    p2.add_argument("--interval", type=int, default=DEFAULT_POLL_INTERVAL_S)
    p2.add_argument("--timeout", type=int, default=DEFAULT_DAEMON_TIMEOUT_S)

    p3 = sub.add_parser("show", help="list queue entries")
    p3.add_argument("--queue-dir", default=None)

    args = parser.parse_args(argv)
    qd = Path(args.queue_dir).expanduser() if args.queue_dir else None

    if args.cmd == "poll":
        stats = poll_once(qd)
        print(json.dumps(stats, indent=2))
        return 0
    if args.cmd == "daemon":
        return run_daemon(qd, poll_interval_s=args.interval, timeout_s=args.timeout)
    if args.cmd == "show":
        qf = (qd or _queue_dir()) / "queue.jsonl"
        if not qf.exists():
            print("(queue empty)")
            return 0
        for e in _load_entries(qf):
            print(json.dumps(e))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
