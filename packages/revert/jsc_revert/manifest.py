"""manifest.py — v4 schema (envelope + per-op payloads).

Per plan-v5 schema spec. Provides:
  - build_envelope(): construct common-envelope dict
  - validate_envelope(): schema check
  - load(snapshot_id): read manifest from disk
  - save(snapshot_dir, manifest): atomic write
  - per-op payload schemas in payloads.py (separate file)

Closes plan-v5 Closure 7 (16 operation_types backed by 8 distinct payload shapes).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import bundle


SCHEMA_VERSION = 4
PARSER_VERSION = "1.0"

VALID_OPERATION_TYPES = (
    "deploy_metadata",
    "deploy_quick_promote",  # v7.17.0 — distinct from deploy_metadata (no metadata-before)
    "data_record_update", "data_record_create", "data_record_upsert",
    "data_record_delete", "data_record_import",
    "data_undelete",  # B4 — undelete a soft-deleted record (revert of data_record_delete soft)
    "data_bulk_update", "data_bulk_upsert", "data_bulk_delete",
    "data_bulk_import",
    "org_assign_permset", "org_assign_permsetlicense",
    "package_install", "package_uninstall",
    "apex_run",
    "revert",
)

VALID_SNAPSHOT_STATUS = (
    "complete", "applied_partial", "pending_finalize_required",
    "async_unfinished", "conflicted_manual_only", "partial",
    "failed", "abandoned", "pre_only", "orphan_failure",
    "polling_exhausted",  # v7.17.0 (Codex-R1-P2-01) — terminal polling state
)


class ManifestValidationError(Exception):
    """Raised when manifest fails schema validation."""


def build_envelope(
    snapshot_id: str,
    operation_type: str,
    wrapper_command: str,
    org_info,
    operator: str,
    session_id: str | None = None,
    parent_snapshot_id: str | None = None,
    invoking_intent: dict | None = None,
    sf_cli_version: str = "",
    api_version: str = "62.0",
) -> dict:
    """Construct the v4 envelope. Caller fills `payload` dict separately."""
    if operation_type not in VALID_OPERATION_TYPES:
        raise ManifestValidationError(
            f"operation_type {operation_type!r} not in VALID_OPERATION_TYPES"
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "manifest_parser_version": PARSER_VERSION,
        "snapshot_id": snapshot_id,
        "captured_at": _iso_now(),
        "snapshot_status": "pre_only",  # caller updates
        "wrapper_command": wrapper_command,
        "operation_type": operation_type,
        "parent_snapshot_id": parent_snapshot_id,
        "invoking_intent": invoking_intent or {
            "kind": "user-direct", "reason": "", "token_fingerprint": None,
        },
        "force_ack_token": None,
        "org": {
            "alias": org_info.alias,
            "org_id_18": org_info.org_id_18,
            "org_id_short": org_info.org_id_short,
            "is_sandbox": org_info.is_sandbox,
            "instance_url": org_info.instance_url,
            "login_url": org_info.login_url,
            "detected_org_type": org_info.detected_org_type,
            "organization_type": getattr(org_info, "organization_type", ""),
            "identity_source": getattr(org_info, "identity_source", ""),
            "org_sequence": None,  # set by org_sequence.acquire_lock
        },
        "operator": operator,
        "session_id": session_id,
        "tooling": {
            "sf_cli_version": sf_cli_version,
            "sfdx_plugin_versions": {},
            "api_version": api_version,
        },
        "phases": {
            "pre_snapshot": {"status": "pending", "duration_seconds": 0, "raw_artifact_paths": []},
            "underlying_command": {"status": "pending", "exit_code": None, "duration_seconds": 0, "raw_artifact_paths": []},
            "post_finalize": {"status": "pending", "duration_seconds": 0, "raw_artifact_paths": []},
        },
        "lock_state": None,  # set by org_sequence.acquire_lock
        "payload": {},  # caller fills per operation_type
        "revert_capabilities": {
            "automatic_revertible": None,  # set by revert_capabilities.compute_revertibility
            "manual_recovery_path": None,
            "side_effects_warning": [],
        },
        "revert": {
            "strategy": None,
            "wrapper_invocation": None,
            "skip_unchanged": True,
            "blocked_by": [],
        },
        "related_lesson_ids": [],
    }


def validate_envelope(manifest: dict) -> None:
    """Validate envelope schema. Raises ManifestValidationError on failure."""
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ManifestValidationError(f"schema_version {manifest.get('schema_version')} != {SCHEMA_VERSION}")
    required = ("snapshot_id", "operation_type", "wrapper_command", "org",
                "operator", "tooling", "phases", "payload", "revert_capabilities", "revert")
    for k in required:
        if k not in manifest:
            raise ManifestValidationError(f"missing required envelope key: {k}")
    if manifest["operation_type"] not in VALID_OPERATION_TYPES:
        raise ManifestValidationError(f"unsupported operation_type: {manifest['operation_type']}")
    status = manifest.get("snapshot_status")
    if status is not None and status not in VALID_SNAPSHOT_STATUS:
        raise ManifestValidationError(f"unsupported snapshot_status: {status}")
    org = manifest["org"]
    if "org_id_18" not in org or len(org.get("org_id_18", "")) != 18:
        raise ManifestValidationError(f"org.org_id_18 missing or wrong length: {org.get('org_id_18')!r}")


def save(snapshot_dir: Path, manifest: dict) -> Path:
    """Atomically save manifest to <snapshot_dir>/manifest.json. Returns the path."""
    validate_envelope(manifest)
    path = snapshot_dir / "manifest.json"
    bundle.atomic_write_json(path, manifest)
    return path


def load(snapshot_dir: Path) -> dict:
    """Load manifest from <snapshot_dir>/manifest.json. Validates schema."""
    path = snapshot_dir / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    validate_envelope(manifest)
    return manifest


def load_by_id(org_id_short: str, alias: str, snapshot_id: str) -> tuple[Path, dict]:
    """Locate and load a snapshot by id. Returns (snapshot_dir, manifest)."""
    org_d = bundle.org_dir(org_id_short, alias)
    short_hash = snapshot_id[:8]
    candidates = sorted(org_d.glob(f"*-{short_hash}"))
    if not candidates:
        raise FileNotFoundError(f"no snapshot found for id {snapshot_id} (short_hash={short_hash}) in {org_d}")
    snap_dir = candidates[-1]  # most recent
    manifest = load(snap_dir)
    if manifest["snapshot_id"] != snapshot_id:
        raise ValueError(f"snapshot_id mismatch: dir contains {manifest['snapshot_id']}, expected {snapshot_id}")
    return snap_dir, manifest


def list_snapshots(org_id_short: str, alias: str, limit: int = 50) -> list[tuple[Path, dict]]:
    """List recent snapshots for an org. Returns [(dir, manifest), ...] newest first."""
    org_d = bundle.org_dir(org_id_short, alias)
    out = []
    # Sort by directory name (which starts with iso-ts) reverse
    for d in sorted(org_d.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        if d.name.startswith("."):
            continue
        try:
            manifest = load(d)
            out.append((d, manifest))
        except (FileNotFoundError, json.JSONDecodeError, ManifestValidationError):
            continue
        if len(out) >= limit:
            break
    return out


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


def get_sf_cli_version() -> str:
    """Best-effort: get sf CLI version string. Returns '' on failure."""
    import subprocess
    try:
        r = subprocess.run(["sf", "--version"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return r.stdout.strip().split()[0] if r.stdout else ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return ""
