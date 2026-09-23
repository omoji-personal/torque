#!/usr/bin/env python3
"""
jsc_revert.mcp_capture — subprocess CLI for JSC MCP server (Node) to call.

Closes Codex-R5-P0-2 Route B per plan-v5 Closure 4 (with Codex-R5-P1-5 fix:
post-capture failures must be visible to caller).

The JSC MCP server (Node, lives in justiceserver-workspace per
mcp-server-discipline.md) shells out to this CLI for write-tool snapshots:

  Node side:
    spawnSync('python3', ['-m', 'jsc_revert.mcp_capture',
                          '--operation', 'apex_run_pre'],
              { input: JSON.stringify(payload), timeout: 25000, encoding: 'utf8' })

  Python side (this CLI):
    Reads JSON payload from stdin → routes to the matching capture handler →
    prints JSON result to stdout. Returns exit 0 on success, exit 1 on
    handler failure. Bridge spec at packages/revert/jsc_revert_bridge.reference.js.

Operations supported (Phase I.3 scope):
  --operation apex_run_pre        snapshot before anonymous Apex run
  --operation apex_run_post       capture after-state + finalize manifest
  --operation apex_run_failed     mark snapshot failed; preserve evidence
  --operation deploy_pre          snapshot before deploy_metadata MCP call
  --operation deploy_post         finalize manifest after successful deploy
  --operation deploy_failed       mark snapshot failed
  --version                       print version + exit (used by bridge startup check)

Phase I.4 will replace the stub handlers with full snapshot machinery from
the jsc_revert package (org_sequence, snapshot_pre, manifest, etc.).
For Phase I.3 the handlers persist a minimal stub manifest to confirm the
bridge round-trip works empirically.
"""

from __future__ import annotations
from jsc_common.workspace import state_dir

import argparse
import hashlib
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

from . import __version__


VALID_OPERATIONS = (
    "apex_run_pre", "apex_run_post", "apex_run_failed",
    "deploy_pre", "deploy_post", "deploy_failed",
)

DEFAULT_REVERT_DIR = None  # compatibility symbol; defaults resolve at call time

# snapshot_id is generated internally as uuid4().hex[:16] for *_pre ops, but the
# *_post / *_failed ops take it from the caller (Node bridge) payload. A
# malicious/buggy payload with snapshot_id='../../etc/x' would escape the revert
# dir via `base / safe_org / snapshot_id` (MCP-PATH-TRAVERSAL, full-repo TAA
# 2026-05-31). Restrict to the hex/uuid shape we actually mint.
_SNAPSHOT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _stub_manifest_dir(target_org: str, snapshot_id: str) -> Path:
    """Phase I.3 stub: write under <selected-client>/state/revert/<org>/<snap>/.
    Phase I.4 will use the full org_id_short-keyed structure.

    Both path segments are sanitized so a caller-supplied snapshot_id (the
    *_post / *_failed handlers read it from the bridge payload) cannot traverse
    out of the revert root."""
    if not isinstance(snapshot_id, str) or not _SNAPSHOT_ID_RE.match(snapshot_id):
        raise ValueError(
            f"invalid snapshot_id {snapshot_id!r} "
            f"(must match {_SNAPSHOT_ID_RE.pattern}; no path separators / traversal)"
        )
    base = state_dir("revert", legacy_env="JSC_REVERT_DIR")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    safe_org = "".join(c if c.isalnum() or c == "-" else "_" for c in target_org)
    snap_dir = base / safe_org / snapshot_id
    # Defense in depth: confirm the resolved path stays under base/safe_org.
    org_root = (base / safe_org).resolve()
    if os.path.commonpath([org_root, snap_dir.resolve()]) != str(org_root):
        raise ValueError(f"snapshot_id {snapshot_id!r} escapes the org snapshot root")
    snap_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return snap_dir


def handle_apex_run_pre(payload: dict) -> dict:
    """Capture pre-run state for an anonymous Apex execution.

    Phase I.3 stub: persist payload + generate snapshot_id. Phase I.4 will
    add real per-touched-object row counts + max LastModifiedDate.
    """
    target_org = payload.get("target_org") or payload.get("targetOrg")
    apex_code = payload.get("apex_code", "")
    if not target_org:
        raise ValueError("apex_run_pre payload missing target_org / targetOrg")

    snapshot_id = uuid.uuid4().hex[:16]
    snap_dir = _stub_manifest_dir(target_org, snapshot_id)
    apex_sha = hashlib.sha256(apex_code.encode()).hexdigest()

    manifest = {
        "schema_version": 4,
        "snapshot_id": snapshot_id,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "snapshot_status": "pre_only",  # post hasn't run yet
        "operation_type": "apex_run",
        "wrapper_command": f"mcp:execute_anonymous_apex --target-org {target_org}",
        "phase": "pre",
        "phase_i3_stub": True,
        "payload": {
            "apex_input_sha256": "sha256:" + apex_sha,
            "apex_input_length_chars": len(apex_code),
            "target_org": target_org,
            "touched_objects_declared": payload.get("touched_objects", []),
            "touch_method": "declared" if payload.get("touched_objects") else "unknown",
        },
    }
    (snap_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    # Also persist apex source for forensic recovery
    (snap_dir / "apex_input.apex").write_text(apex_code, encoding="utf-8")
    return {"snapshot_id": snapshot_id, "status": "pre_only", "manifest_dir": str(snap_dir)}


def handle_apex_run_post(payload: dict) -> dict:
    """Finalize a previously-pre-snapshotted apex run with after-state."""
    snapshot_id = payload.get("snapshot_id")
    target_org = payload.get("target_org") or payload.get("targetOrg")
    if not snapshot_id or not target_org:
        raise ValueError("apex_run_post requires snapshot_id + target_org")

    snap_dir = _stub_manifest_dir(target_org, snapshot_id)
    manifest_path = snap_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"no pre-manifest at {manifest_path}; cannot finalize")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["snapshot_status"] = "complete"
    manifest["phase"] = "complete"
    manifest["finalized_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    manifest["payload"]["result_summary"] = {
        "success": payload.get("result", {}).get("success"),
        "compiled": payload.get("result", {}).get("compiled"),
        "exception_message": payload.get("result", {}).get("exceptionMessage"),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"snapshot_id": snapshot_id, "status": "complete", "manifest_dir": str(snap_dir)}


def handle_apex_run_failed(payload: dict) -> dict:
    """Mark a snapshot failed; preserve pre-state for manual recovery.

    Codex-R6-P1-5 fix: fail closed if no pre-manifest exists. Previously
    silently returned success for unknown snapshot_id, masking integration
    bugs (Node bridge calling failed-handler with wrong/stale snapshot_id).
    Caller MUST receive an error so the operator sees post-capture failure.
    """
    snapshot_id = payload.get("snapshot_id")
    target_org = payload.get("target_org") or payload.get("targetOrg")
    if not snapshot_id or not target_org:
        raise ValueError("apex_run_failed requires snapshot_id + target_org")

    snap_dir = _stub_manifest_dir(target_org, snapshot_id)
    manifest_path = snap_dir / "manifest.json"
    if not manifest_path.exists():
        # Create a partial-manifest "orphan failure" record so operator has
        # SOME evidence the failed-handler was called for this snapshot_id,
        # then raise so the caller knows finalization failed.
        orphan = {
            "schema_version": 4, "snapshot_id": snapshot_id,
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "snapshot_status": "orphan_failure",
            "operation_type": "apex_run",
            "phase": "failed_without_pre_manifest",
            "phase_i3_stub": True,
            "payload": {
                "target_org": target_org,
                "error_message": payload.get("error", ""),
                "warning": "apex_run_failed called for snapshot_id with no pre-manifest; "
                           "integration bug — caller passed unknown/stale snapshot_id",
            },
        }
        manifest_path.write_text(json.dumps(orphan, indent=2), encoding="utf-8")
        raise FileNotFoundError(
            f"apex_run_failed: no pre-manifest for snapshot_id={snapshot_id!r}. "
            f"Orphan failure manifest persisted at {manifest_path} for forensics. "
            f"Caller likely passed a wrong/stale snapshot_id."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["snapshot_status"] = "failed"
    manifest["phase"] = "failed"
    manifest["failed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    manifest["payload"]["error_message"] = payload.get("error", "")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"snapshot_id": snapshot_id, "status": "failed", "manifest_dir": str(snap_dir)}


def handle_deploy_pre(payload: dict) -> dict:
    """Stub: snapshot before deploy_metadata MCP call."""
    target_org = payload.get("target_org") or payload.get("targetOrg")
    if not target_org:
        raise ValueError("deploy_pre payload missing target_org / targetOrg")
    snapshot_id = uuid.uuid4().hex[:16]
    snap_dir = _stub_manifest_dir(target_org, snapshot_id)
    manifest = {
        "schema_version": 4, "snapshot_id": snapshot_id,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "snapshot_status": "pre_only",
        "operation_type": "deploy_metadata",
        "wrapper_command": f"mcp:deploy_metadata --target-org {target_org}",
        "phase": "pre", "phase_i3_stub": True,
        "payload": {
            "target_org": target_org,
            "selectors": {
                "metadata_args": payload.get("metadata", []),
                "manifest_paths": payload.get("manifest", []),
                "source_dirs": payload.get("source_dir", []),
            },
        },
    }
    (snap_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"snapshot_id": snapshot_id, "status": "pre_only", "manifest_dir": str(snap_dir)}


def handle_deploy_post(payload: dict) -> dict:
    snapshot_id = payload.get("snapshot_id")
    target_org = payload.get("target_org") or payload.get("targetOrg")
    if not snapshot_id or not target_org:
        raise ValueError("deploy_post requires snapshot_id + target_org")
    snap_dir = _stub_manifest_dir(target_org, snapshot_id)
    manifest_path = snap_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"no pre-manifest at {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["snapshot_status"] = "complete"
    manifest["phase"] = "complete"
    manifest["finalized_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    manifest["payload"]["deploy_id"] = payload.get("deploy_id")
    manifest["payload"]["deploy_status_code"] = payload.get("status")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"snapshot_id": snapshot_id, "status": "complete", "manifest_dir": str(snap_dir)}


def handle_deploy_failed(payload: dict) -> dict:
    """Mark a deploy snapshot failed. Codex-R6-P1-5 fix: fail closed if no pre-manifest."""
    snapshot_id = payload.get("snapshot_id")
    target_org = payload.get("target_org") or payload.get("targetOrg")
    if not snapshot_id or not target_org:
        raise ValueError("deploy_failed requires snapshot_id + target_org")
    snap_dir = _stub_manifest_dir(target_org, snapshot_id)
    manifest_path = snap_dir / "manifest.json"
    if not manifest_path.exists():
        orphan = {
            "schema_version": 4, "snapshot_id": snapshot_id,
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "snapshot_status": "orphan_failure",
            "operation_type": "deploy_metadata",
            "phase": "failed_without_pre_manifest",
            "phase_i3_stub": True,
            "payload": {
                "target_org": target_org,
                "error_message": payload.get("error", ""),
                "warning": "deploy_failed called for snapshot_id with no pre-manifest; "
                           "integration bug — caller passed unknown/stale snapshot_id",
            },
        }
        manifest_path.write_text(json.dumps(orphan, indent=2), encoding="utf-8")
        raise FileNotFoundError(
            f"deploy_failed: no pre-manifest for snapshot_id={snapshot_id!r}. "
            f"Orphan failure manifest persisted at {manifest_path} for forensics."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["snapshot_status"] = "failed"
    manifest["phase"] = "failed"
    manifest["failed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    manifest["payload"]["error_message"] = payload.get("error", "")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"snapshot_id": snapshot_id, "status": "failed", "manifest_dir": str(snap_dir)}


HANDLERS = {
    "apex_run_pre": handle_apex_run_pre,
    "apex_run_post": handle_apex_run_post,
    "apex_run_failed": handle_apex_run_failed,
    "deploy_pre": handle_deploy_pre,
    "deploy_post": handle_deploy_post,
    "deploy_failed": handle_deploy_failed,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="JSC MCP server Route B bridge")
    parser.add_argument("--operation", choices=VALID_OPERATIONS,
                        help="snapshot operation to perform")
    parser.add_argument("--version", action="store_true",
                        help="print version + exit (used by bridge startup check)")
    args = parser.parse_args()

    if args.version:
        print(f"jsc_revert.mcp_capture {__version__}")
        return 0

    if not args.operation:
        sys.stderr.write("error: --operation required (or --version)\n")
        return 1

    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.stderr.write("error: empty stdin payload\n")
            return 1
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"error: invalid JSON on stdin: {e}\n")
        return 1

    handler = HANDLERS[args.operation]
    try:
        result = handler(payload)
    except Exception as e:
        sys.stderr.write(f"error: {args.operation} handler failed: {type(e).__name__}: {e}\n")
        return 1

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
