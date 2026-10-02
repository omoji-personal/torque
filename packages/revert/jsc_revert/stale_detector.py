"""Compare the current recovery scope with its captured post-deployment state."""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
from enum import Enum
from pathlib import Path
from typing import NamedTuple

from . import snapshot_pre
from .metadata_scope import (MetadataScopeError, selected_records, _file_index,
                             _component_paths, _logical_relative)


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


def _observe(snapshot_dir: Path, manifest: dict, target_org: str, *, prefix: str) -> list[dict]:
    records = selected_records(manifest.get("payload", {}))
    keys = sorted({(entry["type"], entry["fullName"]) for entry in records})
    if not keys:
        raise MetadataScopeError("Recovery scope is empty")
    # Never reuse retrieved files from an earlier verification.
    output = Path(tempfile.mkdtemp(prefix=prefix, dir=snapshot_dir)).resolve()
    retrieved = snapshot_pre.run_pre_snapshot_retrieve(
        [f"{kind}:{name}" for kind, name in keys], target_org, output, timeout_seconds=180)
    if retrieved.status != "Succeeded":
        raise MetadataScopeError("Retrieval did not succeed")
    captured = [{"type": fc.type, "fullName": fc.fullName, "before_state": fc.state,
                 "before_checksum": fc.checksum, "filePath": fc.file_path}
                for fc in retrieved.files]
    index = _file_index({"files": captured}, output)
    observations = []
    for kind, name in keys:
        matching = [fc for fc in retrieved.files if (fc.type, fc.fullName) == (kind, name)]
        if not matching or any(fc.state not in {"present", "absent"} for fc in matching):
            raise MetadataScopeError(f"Current state unavailable for {kind}:{name}")
        states = {fc.state for fc in matching}
        if len(states) != 1:
            raise MetadataScopeError(f"Contradictory retrieval for {kind}:{name}")
        state = matching[0].state
        checksum = None
        if state == "present":
            paths = set()
            for fc in matching:
                if not fc.file_path or not fc.checksum:
                    raise MetadataScopeError(f"Current checksum unavailable for {kind}:{name}")
                paths.update(_component_paths({"type": kind, "fullName": name},
                             Path(fc.file_path), output, index))
            # Include companion files and bundle members, not only the last CLI row.
            content = sorted((_logical_relative(path, output).as_posix(), index[path]) for path in paths)
            checksum = hashlib.sha256(json.dumps(content).encode("utf-8")).hexdigest()
        observations.append({"type": kind, "fullName": name, "state": state, "checksum": checksum})
    return observations


def capture_deployment_state(snapshot_dir: Path, manifest: dict, target_org: str) -> None:
    """Called under the write lease immediately after a successful deployment."""
    manifest["payload"]["after_state"] = _observe(snapshot_dir, manifest, target_org, prefix="metadata-after-")


def classify_metadata_drift(snapshot_dir: Path, manifest: dict, target_org: str) -> list[ComponentDrift]:
    try:
        records = selected_records(manifest.get("payload", {}))
        keys = {(entry["type"], entry["fullName"]) for entry in records}
        after = manifest.get("payload", {}).get("after_state")
        if not isinstance(after, list) or not after:
            raise MetadataScopeError("No captured post-deployment state; legacy recovery requires manual review")
        baseline = {(entry["type"], entry["fullName"]): entry for entry in after}
        if set(baseline) != keys or len(baseline) != len(after):
            raise MetadataScopeError("Post-deployment capture does not match the recovery scope")
        for entry in baseline.values():
            if (entry.get("state") not in {"present", "absent"}
                    or (entry["state"] == "present" and (not isinstance(entry.get("checksum"), str)
                        or not re.fullmatch(r"[0-9a-f]{64}", entry["checksum"])))
                    or (entry["state"] == "absent" and entry.get("checksum") is not None)):
                raise MetadataScopeError("Post-deployment state is incomplete")
        current = _observe(snapshot_dir, manifest, target_org, prefix=".drift-")
    except Exception as exc:
        return [ComponentDrift("", "", DriftState.RETRIEVE_FAILED, None, None, str(exc))]
    drift = []
    for entry in current:
        expected = baseline[(entry["type"], entry["fullName"])]
        if entry["state"] == expected["state"] and entry["checksum"] == expected["checksum"]:
            state = DriftState.PRESENT_SAME
        elif entry["state"] == "absent":
            state = DriftState.ABSENT
        else:
            state = DriftState.PRESENT_CHANGED
        drift.append(ComponentDrift(entry["type"], entry["fullName"], state,
                                    expected["checksum"], entry["checksum"], None))
    return drift


def is_blocking(drift_list: list[ComponentDrift]) -> bool:
    return not drift_list or any(d.state != DriftState.PRESENT_SAME for d in drift_list)


def is_unknown(drift_list: list[ComponentDrift]) -> bool:
    return not drift_list or any(d.state in {DriftState.RETRIEVE_FAILED, DriftState.RENAMED_UNKNOWN}
                                for d in drift_list)


def summary(drift_list: list[ComponentDrift]) -> dict[str, int]:
    out = {state.value: 0 for state in DriftState}
    for drift in drift_list:
        out[drift.state.value] += 1
    return out
