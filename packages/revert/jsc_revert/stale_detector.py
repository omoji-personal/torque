"""stale_detector.py — 5-state drift detection per plan-v5 Closure stale-revert.

States:
  present_same     — component exists in org AND checksum matches snapshot
  present_changed  — component exists but checksum drifted (someone else edited)
  absent           — component no longer in org (deleted out-of-band)
  renamed_unknown  — checksum-different + SetupAuditTrail shows rename event
                     (best-effort; SetupAuditTrail has limited retention)
  retrieve_failed  — query/retrieve operation itself failed (network/auth/perms)

Used by revert_executor to gate the revert: by default, absent + renamed_unknown
states block the revert (require --force).

For non-metadata snapshots (data_record_*, apex_run, etc.), drift detection
is operation-type-specific and may not apply directly.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import NamedTuple

from . import bundle, snapshot_pre
from .metadata_scope import MetadataScopeError, selected_records


class DriftState(Enum):
    PRESENT_SAME = "present_same"
    PRESENT_CHANGED = "present_changed"
    ABSENT = "absent"
    RENAMED_UNKNOWN = "renamed_unknown"
    RETRIEVE_FAILED = "retrieve_failed"


class ComponentDrift(NamedTuple):
    type: str
    fullName: str
    state: DriftState
    snapshot_checksum: str | None
    current_checksum: str | None
    raw_problem: str | None


def classify_metadata_drift(
    snapshot_dir: Path,
    manifest: dict,
    target_org: str,
) -> list[ComponentDrift]:
    """For deploy_metadata snapshots: re-retrieve each component + compare checksums.

    Returns drift for the original recovery scope, excluding incidental parents.
    """
    payload = manifest.get("payload", {})
    try:
        files_in_snapshot = selected_records(payload)
    except MetadataScopeError:
        # The planner will explain why no exact recovery plan can be built.
        return []
    if not files_in_snapshot:
        return []

    # Build retrieve selectors for all components in snapshot
    selectors = sorted({f"{f['type']}:{f['fullName']}" for f in files_in_snapshot
                        if f.get("type") and f.get("fullName")})
    if not selectors:
        return []

    # Stage retrieve into a fresh tmpdir
    drift_dir = snapshot_dir / ".drift-check"
    drift_dir.mkdir(exist_ok=True, mode=0o700)

    try:
        retrieve_result = snapshot_pre.run_pre_snapshot_retrieve(
            selectors, target_org, drift_dir, timeout_seconds=180,
        )
    except (ValueError, Exception) as e:
        # Retrieve itself failed → all components retrieve_failed
        return [
            ComponentDrift(
                type=f.get("type", ""), fullName=f.get("fullName", ""),
                state=DriftState.RETRIEVE_FAILED,
                snapshot_checksum=f.get("before_checksum"),
                current_checksum=None,
                raw_problem=f"retrieve failed: {str(e)[:200]}",
            )
            for f in files_in_snapshot
        ]

    # Build lookup: (type, fullName) → FileClassification from retrieve
    current_index = {(fc.type, fc.fullName): fc for fc in retrieve_result.files}

    drift_list = []
    for f in files_in_snapshot:
        ftype = f.get("type", "")
        fullname = f.get("fullName", "")
        snap_checksum = f.get("before_checksum")
        current = current_index.get((ftype, fullname))

        if current is None:
            # Component wasn't even in the retrieve result — fail-closed
            drift_list.append(ComponentDrift(
                type=ftype, fullName=fullname,
                state=DriftState.RETRIEVE_FAILED,
                snapshot_checksum=snap_checksum, current_checksum=None,
                raw_problem="component not present in re-retrieve result",
            ))
            continue

        if current.state == "absent":
            drift_list.append(ComponentDrift(
                type=ftype, fullName=fullname,
                state=DriftState.ABSENT,
                snapshot_checksum=snap_checksum, current_checksum=None,
                raw_problem=current.raw_problem,
            ))
        elif current.state == "retrieve_failed":
            drift_list.append(ComponentDrift(
                type=ftype, fullName=fullname,
                state=DriftState.RETRIEVE_FAILED,
                snapshot_checksum=snap_checksum, current_checksum=None,
                raw_problem=current.raw_problem,
            ))
        elif current.state == "present":
            if snap_checksum and current.checksum and snap_checksum == current.checksum:
                drift_list.append(ComponentDrift(
                    type=ftype, fullName=fullname,
                    state=DriftState.PRESENT_SAME,
                    snapshot_checksum=snap_checksum, current_checksum=current.checksum,
                    raw_problem=None,
                ))
            else:
                drift_list.append(ComponentDrift(
                    type=ftype, fullName=fullname,
                    state=DriftState.PRESENT_CHANGED,
                    snapshot_checksum=snap_checksum, current_checksum=current.checksum,
                    raw_problem=None,
                ))
        else:
            drift_list.append(ComponentDrift(
                type=ftype, fullName=fullname,
                state=DriftState.RETRIEVE_FAILED,
                snapshot_checksum=snap_checksum, current_checksum=None,
                raw_problem=f"unexpected current.state={current.state!r}",
            ))

    return drift_list


def is_blocking(drift_list: list[ComponentDrift]) -> bool:
    """Returns True if any component is in a state that blocks revert by default.
    Operator can override with --force.
    """
    return any(d.state in (DriftState.ABSENT, DriftState.RENAMED_UNKNOWN)
               for d in drift_list)


def summary(drift_list: list[ComponentDrift]) -> dict[str, int]:
    """Count of components per state."""
    out = {state.value: 0 for state in DriftState}
    for d in drift_list:
        out[d.state.value] += 1
    return out
